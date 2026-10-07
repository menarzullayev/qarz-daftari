# Evidence Record

- Evidence ID: EVID-076
- CLAIM: An end-to-end suite of 14 browser tests drives the built front end against the real API, worker and database through the nginx proxy (pull request 65) and passed CI on main in the run for commit 6bfe5ff. Running the two halves together found three defects that the fake-server tests had not: the administrators' panel could not be reached through the proxy; reloading the Mini App signed the person out, because sign-in data is now accepted once; and an oversized request got the proxy's HTML page instead of JSON over HTTP/2. All three were fixed. The panels' Content-Security-Policy no longer allows eval. The real Telegram widget and a real Telegram client were not used: sign-in was done with correctly signed stand-in data
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/65 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37664777687
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
