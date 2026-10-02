// Evergreen dev harness: tells Firefox to run evergreen-dev.cfg at startup.
// Installed into the dev copy's defaults/pref/ by `eg.py dev`. DEV ONLY.
pref("general.config.filename", "evergreen-dev.cfg");
pref("general.config.obscure_value", 0);
pref("general.config.sandbox_enabled", false);
