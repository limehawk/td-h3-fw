# Stock TIDRADIO firmware

Downloaded 2026-11-03 from TIDRADIO. None of these files are in git (see `.gitignore`).
Kept for recovery and as the reference this firmware's hardware setup was read from.
Original zip SHA-256s: `zips.sha256` (the zips themselves were unpacked here and removed).

| Radio | Version | Cable | Image | Notes |
|---|---|---|---|---|
| **TD-H3** | **250317** (also 250110, 240817) | USB-C | `td-h3/250317/H3_250317.BIN` (61,671 B) | **The one for this radio.** Plain 8051 image, LJMP at 0. Flash with `tools/tdh3 firmware flash` to go back to stock. `TD-H3_IAP_setup240305.exe` is TIDRADIO's Windows updater. |
| TD-H9 | 1.0.38, 1.0.32 | CH340 `.bin`, Type-C `.fw` | `td-h9/<ver>/<cable>/` | Different radio. Not for the TD-H3. |

| SHA-256 | File |
|---|---|
| fb907ac359342c4ddc78ede78c27320548135ce06b82e752c3d68f737050d954 | td-h3/250317/H3_250317.BIN |
| 229f83a2bccb38a72b186ef3ffc932283d4978b1b126606c94f5de1a07ba70cd | td-h3/250317/H3_250110.BIN |
| ea810b4389cc52d30d2e1c7760572e408e1c673b1585543852de463c79dcddf3 | td-h3/250317/H3_240817.BIN |

TIDRADIO's note for the H9: back up memory channels and factory reset after updating.

Where to get them: TIDRADIO's firmware page, https://tidradio.com/pages/tidradio-firmware,
or the direct links below. Check downloads against the SHA-256s above and `zips.sha256`.

Source URLs:

- TD-H3 250317: https://cdn.shopify.com/s/files/1/0096/6479/0607/files/H3_Firmware_Upgrade_d8029781-3607-44e9-95e7-6e58eacff50f.zip?v=1774862364
- TD-H9 1.0.38 CH340: https://cdn.shopify.com/s/files/1/0096/6479/0607/files/H9_Update-CH340_7e2bdcb5-731d-4976-850d-f7954c0e7a0a.zip?v=1792395233
- TD-H9 1.0.38 Type-C: https://cdn.shopify.com/s/files/1/0096/6479/0607/files/H9_Update-Type-C_41dc2974-af11-4ae0-8302-3fe4d775de0c.zip?v=1792395509
- TD-H9 1.0.32 CH340: https://cdn.shopify.com/s/files/1/0096/6479/0607/files/H9_Update-CH340_Tutorial_bc8758da-4c46-49f4-a04f-affb2b7657d6.zip?v=1778123747
- TD-H9 1.0.32 Type-C: https://cdn.shopify.com/s/files/1/0096/6479/0607/files/H9_Update-Type-C_3fc0dc10-39af-4ae9-8044-2f0757bf7c74.zip?v=1778123734
