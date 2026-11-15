# Third-party notices

## frida-objc-bridge

`src/moneywiz_mcp/agent.js` is a compiled bundle that includes
[frida-objc-bridge](https://github.com/frida/frida-objc-bridge) 8.0.6,
licensed under `LGPL-2.0 WITH WxWindows-exception-3.1` (the wxWindows Library
Licence). The exception permits distributing works that include the library
under the distributor's own terms. Its unmodified source is available from the
link above and is installed by `npm ci` in `agent/`.

## Frida

[Frida](https://frida.re) is a runtime dependency installed from PyPI
(`frida`), licensed under the wxWindows Library Licence 3.1. It is not bundled.

## MoneyWiz

MoneyWiz is a product of SilverWiz. This project is not affiliated with or
endorsed by SilverWiz. `src/moneywiz_mcp/signatures.json` records method names
and type encodings observed in the running app, used only to check calls for
compatibility.
