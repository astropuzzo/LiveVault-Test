from . import main
from app.stripchat_preview import stripchat_preview_worker


with stripchat_preview_worker():
    raise SystemExit(main())
