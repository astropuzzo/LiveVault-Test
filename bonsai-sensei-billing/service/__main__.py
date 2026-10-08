import os

import uvicorn

uvicorn.run("service.api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8095")),
            workers=1, access_log=False, proxy_headers=False, log_level="warning")
