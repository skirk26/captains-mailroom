# Vendored libraries

Served from this folder so the Content-Security-Policy can allow only this
site's own scripts and styles. Files are unmodified copies; check the hash
after replacing one.

| File | Version | Source | sha384 |
|---|---|---|---|
| `bootstrap-5.3.3/bootstrap.min.css` | 5.3.3 | https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css | `QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH` |
| `html5-qrcode-2.3.8/html5-qrcode.min.js` | 2.3.8 | https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js | `c9d8RFSL+u3exBOJ4Yp3HUJXS4znl9f+z66d1y54ig+ea249SpqR+w1wyvXz/lk+` |

html5-qrcode bundles ZXing, which reads the DataMatrix codes on Amazon labels,
so no separate DataMatrix library is needed.

To check a hash:

```bash
openssl dgst -sha384 -binary <file> | openssl base64 -A
```
