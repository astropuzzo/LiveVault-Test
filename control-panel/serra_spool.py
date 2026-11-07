"""Verified, restart-safe ingestion of autonomous camera SD captures."""
import hashlib
import json
import os
import re
import time

NAME = re.compile(r'(?P<ts>\d{10})-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-r(?P<rotation>0|90|180|270)-t(?P<tier>[rbh])\.jpg')
ERRORS = {'sd_unavailable':'MicroSD non disponibile: salvataggio diretto sul server quando possibile.',
          'queue_full':'MicroSD al limite: le foto non ancora trasferite restano protette.',
          'snapshot_error':'Scatto sulla camera non riuscito: nuovo tentativo automatico.',
          'frozen_snapshot':'La camera restituisce un fotogramma fermo: recupero automatico in corso.',
          'sd_write_error':'Scrittura microSD non riuscita: salvataggio diretto sul server quando possibile.',
          'usage_error':'Controllo spazio microSD non disponibile.',
          'recovery_error':'Una foto sulla microSD richiede un nuovo controllo.'}


def enabled(camera):
    return camera._read(camera.CONFIG).get('sd_spool') is True


def request(camera, operation, *, body=None, name=None):
    params = {'op':operation}
    if name is not None:
        params['name'] = name
    with camera._upstream('serra-spool',body=body,**params) as response:
        data = response.read(32769)
    if len(data)>32768:
        raise camera.SerraError('Risposta microSD troppo grande.',502)
    return data


def configure(camera):
    config = camera.settings()
    quota = camera._read(camera.CONFIG).get('sd_quota_gb',8)
    if type(quota) is not int or not 1<=quota<=16:
        raise camera.SerraError('Quota microSD non valida.')
    body = f"{int(config['enabled'])} {config['interval_seconds']} {config['rotation_degrees']} {quota*1024**2}\n".encode()
    try:
        result = json.loads(request(camera,'config',body=body))
        if result.get('ok') is not True: raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise camera.SerraError('Impostazioni microSD non confermate dalla camera.',502) from None
    camera._state(sd_settings_synced=time.time())


def manual_capture(camera):
    configure(camera)
    try:
        result=json.loads(request(camera,'capture',body=b''))
        if result.get('ok') is not True: raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise camera.SerraError('Scatto sulla microSD non confermato.',502) from None
    return {'ok':True,'queued':True}


def manifest(camera):
    try:
        lines=request(camera,'manifest').decode('ascii').splitlines()
        status=json.loads(lines[0])
        keys=('enabled','interval','rotation','quota_kb','used_kb','pending','last_capture')
        if status.get('ok') is not True or type(status.get('online')) is not bool:
            raise ValueError()
        if any(type(status.get(key)) is not int or status[key]<0 for key in keys) or not isinstance(status.get('error',''),str):
            raise ValueError()
        if status['enabled'] not in (0,1) or status['interval'] not in camera.INTERVALS or status['rotation'] not in camera.ROTATIONS:
            raise ValueError()
        entries=[]; names=set()
        if len(lines)>25: raise ValueError()
        for line in lines[1:]:
            name,size,digest=line.split()
            match=NAME.fullmatch(name)
            size=int(size)
            if not match or not 4<size<=camera.MAX_IMAGE or not re.fullmatch('[0-9a-f]{64}',digest) or name in names:
                raise ValueError()
            if not 1600000000<=int(match['ts'])<=time.time()+300: raise ValueError()
            names.add(name);entries.append({'name':name,'size':size,'digest':digest,
                                           'ts':int(match['ts']),'rotation':int(match['rotation']),'tier':match['tier']})
    except (ValueError,KeyError,IndexError,TypeError,UnicodeError,AttributeError):
        raise camera.SerraError('Indice microSD non valido: nessuna foto confermata o eliminata.',502) from None
    return status,entries


def _commit(camera, root, entry, raw):
    if len(raw)!=entry['size'] or hashlib.sha256(raw).hexdigest()!=entry['digest'] or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
        raise camera.SerraError('Foto microSD incompleta o diversa dall’originale: resta sulla camera.',502)
    image=camera.rotate_jpeg(raw,entry['rotation'])
    # Stable path makes an interrupted write replayable without creating another frame.
    name=str(entry['ts']*10**9+int(hashlib.sha256(entry['name'].encode()).hexdigest()[:7],16))+'.jpg'
    with camera._lock('media'):
        tiers=['recent'] if entry['tier'] in ('r','b') else []
        if entry['tier'] in ('h','b'): tiers.append('history')
        candidates=[root/'pending'/('h'+name if tier=='history' else name) for tier in tiers]
        if any(path.is_symlink() for path in candidates): raise camera.SerraError('Percorso di importazione non valido.')
        camera._room(root,sum(len(image) for path in candidates if not path.exists()),{path.name for path in candidates})
        targets=[]
        for tier in tiers:
            target=root/'pending'/('h'+name if tier=='history' else name)
            if target.is_symlink(): raise camera.SerraError('Percorso di importazione non valido.')
            if target.exists():
                if target.read_bytes()!=image: raise camera.SerraError('Conflitto tra foto importate: originale microSD conservato.')
            else:
                temporary=target.with_suffix('.part')
                try:
                    with temporary.open('xb') as output:
                        output.write(image);output.flush();os.fsync(output.fileno())
                    os.replace(temporary,target)
                finally:
                    temporary.unlink(missing_ok=True)
            targets.append((target,tier))
        directory=os.open(root/'pending',os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        with camera._db() as db:
            for target,tier in targets:
                db.execute('INSERT OR IGNORE INTO frames(name,ts,size,rotation,tier) VALUES(?,?,?,?,?)',
                           (target.name,entry['ts'],len(image),entry['rotation'],tier))
            db.execute('INSERT INTO spool_receipts(name,digest,ts) VALUES(?,?,?)',(entry['name'],entry['digest'],entry['ts']))
    previous=camera._state()
    camera._state(last_capture=max(previous.get('last_capture',0),entry['ts']),last_error='',last_sd_import=time.time())


def sync(camera, *, import_media=True):
    """Only acknowledge after durable JPEGs + database commit. Pause import without NVMe."""
    with camera._lock('capture',blocking=False):
        status,entries=manifest(camera)
        config=camera.settings();quota=camera._read(camera.CONFIG).get('sd_quota_gb',8)*1024**2
        if (status['enabled'],status['interval'],status['rotation'],status['quota_kb']) != (int(config['enabled']),config['interval_seconds'],config['rotation_degrees'],quota):
            configure(camera)
        error=ERRORS.get(status.get('error'),'Controllo microSD in corso.' if status.get('error') else '')
        camera._state(sd_status=status,sd_error=error,last_sd_contact=time.time())
        if not entries or not import_media:
            return status
        acknowledgements=[];failure=None
        with camera.storage(write=True) as root:
            for entry in entries:
                if camera._quiescing(): break
                with camera._db() as db:
                    receipt=db.execute('SELECT digest FROM spool_receipts WHERE name=?',(entry['name'],)).fetchone()
                if receipt:
                    if receipt['digest']!=entry['digest']:
                        raise camera.SerraError('Una foto microSD è cambiata dopo il trasferimento: conservata sulla camera.')
                else:
                    try:
                        with camera._upstream('serra-spool',op='get',name=entry['name']) as response:
                            raw=response.read(camera.MAX_IMAGE+1)
                        if camera._quiescing(): break
                        _commit(camera,root,entry,raw)
                    except camera.SerraError as exc:
                        failure=exc
                        if exc.status!=502: break
                        continue
                acknowledgements.append(entry)
            if acknowledgements and not camera._quiescing():
                body=''.join(f"{entry['name']} {entry['digest']}\n" for entry in acknowledgements).encode()
                try:
                    result=json.loads(request(camera,'ack',body=body))
                    if result.get('ok') is not True: raise ValueError()
                except (ValueError,TypeError,AttributeError):
                    raise camera.SerraError('Conferma microSD non ricevuta: trasferimento ripetibile senza duplicati.',502) from None
                with camera._db() as db:
                    db.executemany('UPDATE spool_receipts SET acked_at=? WHERE name=?',[(time.time(),entry['name']) for entry in acknowledgements])
                    db.execute('DELETE FROM spool_receipts WHERE acked_at IS NOT NULL AND acked_at<?',(time.time()-86400,))
                camera._state(last_sd_sync=time.time(),sd_error=error)
        if failure: raise failure
        return status
