# Sources and third-party components

The Python loader is an original implementation. Memory layout constants are sourced from Smithbox's Sekiro 1.6 profile. Format layout and pointer traversal were cross-checked against the following public source repositories. Copies downloaded for reference are in the source workspace's `reference/` directory; they are not compiled into the loader.

- Smithbox: https://github.com/vawser/Smithbox
  - Profile: `Smithbox.Release/Output/Assets/PARAM/SDT/Param Reloader/1_6_0_0.json`
- DSMapStudio: https://github.com/soulsmods/DSMapStudio
  - Reference commit: `97bc264cbc3e82a783eb017f57edf651f8ba000b`
  - MIT license; see `reference/DSMapStudio-LICENSE` in the source workspace.
- SoulsFormats: https://github.com/soulsmods/SoulsFormats
  - Reference commit: `ee1dd61958f60bdc51ce3da548e9a90a8ab39905`
  - GPL-3.0 license; see `reference/SoulsFormats-LICENSE` in the source workspace. This implementation does not copy or link SoulsFormats code.
- FromSoftware-rs: https://github.com/vswarte/fromsoftware-rs
  - Reference commit: `6933967b176332a37e9ce6773f8dcd74b9feaf42`
  - This fixed commit uses `LICENSE-MIT-NON-AI` and `LICENSE-ASL2-NON-AI`; see the exact notices in `reference/`. Runtime binary-layout and ABI facts were consulted; Rust code is not copied into or linked with the implementation.

The executable is packaged with PyInstaller 6.22.3 and Python 3.13. Bundled runtime license notices are retained in the distribution when supplied by the packager. PyInstaller's bootloader exception permits distribution of bundled applications. See https://pyinstaller.org/en/stable/license.html and https://docs.python.org/3/license.html .

Oodle is proprietary software owned by its respective rights holders. `oo2core_6_win64.dll` is loaded from the user's existing Sekiro installation; no game DLL, game PARAM data, or executable is included in this distribution.

The original native module is compiled with Zig 0.16.0 using its Windows GNU target and bundled MinGW headers/runtime. Zig is a build dependency. Available notices are included in licenses/. See https://ziglang.org/ and https://github.com/mingw-w64/mingw-w64 .
