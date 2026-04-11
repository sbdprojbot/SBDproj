Using cached wrapt-2.1.2-cp314-cp314-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (120 kB)
Using cached frozenlist-1.8.0-cp314-cp314-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (231 kB)
Using cached future-1.0.0-py3-none-any.whl (491 kB)
Using cached google_auth-2.49.2-py3-none-any.whl (240 kB)
Using cached cryptography-46.0.7-cp311-abi3-manylinux_2_34_x86_64.whl (4.5 MB)
Using cached cffi-2.0.0-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (219 kB)
Using cached google_auth_oauthlib-1.3.1-py3-none-any.whl (19 kB)
Using cached h11-0.16.0-py3-none-any.whl (37 kB)
Using cached httplib2-0.31.2-py3-none-any.whl (91 kB)
Using cached pyparsing-3.3.2-py3-none-any.whl (122 kB)
Using cached itsdangerous-2.2.0-py3-none-any.whl (16 kB)
Using cached jinja2-3.1.6-py3-none-any.whl (134 kB)
Using cached markupsafe-3.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (23 kB)
Using cached propcache-0.4.1-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (201 kB)
Using cached pyasn1-0.6.3-py3-none-any.whl (83 kB)
Using cached pyasn1_modules-0.4.2-py3-none-any.whl (181 kB)
Using cached requests_oauthlib-2.0.0-py2.py3-none-any.whl (24 kB)
Using cached oauthlib-3.3.1-py3-none-any.whl (160 kB)
Using cached rsa-4.9.1-py3-none-any.whl (34 kB)
Using cached six-1.17.0-py2.py3-none-any.whl (11 kB)
Using cached tqdm-4.67.3-py3-none-any.whl (78 kB)
Using cached typing_inspection-0.4.2-py3-none-any.whl (14 kB)
Using cached werkzeug-3.1.8-py3-none-any.whl (226 kB)
Using cached packaging-26.0-py3-none-any.whl (74 kB)
Using cached pycparser-3.0-py3-none-any.whl (48 kB)
Using cached sniffio-1.3.1-py3-none-any.whl (10 kB)
Installing collected packages: aenum, wrapt, urllib3, typing-extensions, tqdm, sniffio, six, pyparsing, pycparser, pyasn1, propcache, packaging, oauthlib, multidict, markupsafe, jiter, itsdangerous, idna, h11, future, frozenlist, distro, click, charset_normalizer, certifi, blinker, attrs, annotated-types, aiohappyeyeballs, yarl, werkzeug, typing-inspection, rsa, requests, python_dateutil, pydantic-core, pyasn1-modules, jinja2, httplib2, httpcore, gunicorn, Deprecated, cffi, anyio, aiosignal, requests-oauthlib, pydantic, oauth2client, httpx, flask, cryptography, aiohttp, openai, line-bot-sdk, google-auth, google-auth-oauthlib, gspread
Successfully installed Deprecated-1.3.1 aenum-3.1.17 aiohappyeyeballs-2.6.1 aiohttp-3.13.5 aiosignal-1.4.0 annotated-types-0.7.0 anyio-4.13.0 attrs-26.1.0 blinker-1.9.0 certifi-2026.2.25 cffi-2.0.0 charset_normalizer-3.4.7 click-8.3.2 cryptography-46.0.7 distro-1.9.0 flask-3.1.3 frozenlist-1.8.0 future-1.0.0 google-auth-2.49.2 google-auth-oauthlib-1.3.1 gspread-6.2.1 gunicorn-25.3.0 h11-0.16.0 httpcore-1.0.9 httplib2-0.31.2 httpx-0.28.1 idna-3.11 itsdangerous-2.2.0 jinja2-3.1.6 jiter-0.14.0 line-bot-sdk-3.23.0 markupsafe-3.0.3 multidict-6.7.1 oauth2client-4.1.3 oauthlib-3.3.1 openai-2.31.0 packaging-26.0 propcache-0.4.1 pyasn1-0.6.3 pyasn1-modules-0.4.2 pycparser-3.0 pydantic-2.12.5 pydantic-core-2.41.5 pyparsing-3.3.2 python_dateutil-2.9.0.post0 requests-2.33.1 requests-oauthlib-2.0.0 rsa-4.9.1 six-1.17.0 sniffio-1.3.1 tqdm-4.67.3 typing-extensions-4.15.0 typing-inspection-0.4.2 urllib3-2.6.3 werkzeug-3.1.8 wrapt-2.1.2 yarl-1.23.0
[notice] A new release of pip is available: 25.3 -> 26.0.1
[notice] To update, run: pip install --upgrade pip
==> Uploading build...
==> Uploaded in 9.8s. Compression took 5.3s
==> Build successful 🎉
==> Deploying...
==> Setting WEB_CONCURRENCY=1 by default, based on available CPUs in the instance
==> Running 'gunicorn app:app -b 0.0.0.0:10000'
[2026-04-11 11:09:46 +0000] [44] [INFO] Starting gunicorn 25.3.0
[2026-04-11 11:09:46 +0000] [44] [INFO] Listening at: http://0.0.0.0:10000 (44)
[2026-04-11 11:09:46 +0000] [44] [INFO] Using worker: sync
[2026-04-11 11:09:46 +0000] [45] [INFO] Booting worker with pid: 45
Menu
[2026-04-11 11:09:46 +0000] [44] [INFO] Control socket listening at /opt/render/.gunicorn/gunicorn.ctl
[2026-04-11 11:10:18 +0000] [44] [INFO] Handling signal: term
[2026-04-11 11:10:18 +0000] [45] [INFO] Worker exiting (pid: 45)
[2026-04-11 11:10:21 +0000] [44] [INFO] Shutting down: Master
==> Port scan timeout reached, no open ports detected. Bind your service to at least one port. If you don't need to receive traffic on any port, create a background worker instead.
==> Docs on specifying a port: https://render.com/docs/web-services#port-binding
==> Timed Out
==> Common ways to troubleshoot your deploy: https://render.com/docs/troubleshooting-deploys