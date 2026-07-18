DPI World Settings Icon Directory
==============================

Place PNG icon files here for higher quality display.
Files should be named by world setting key.

Example:
  day.png       -> Day length icon
  bees.png      -> Bees setting icon
  bearger.png   -> Bearger setting icon
  winter.png    -> Winter season icon

How to extract icons from DST game files:
==========================================

DST icons are stored in .tex (texture) and .xml (atlas) files at:
  <Steam>\steamapps\common\Don't Starve Together\data\images\

Key files for world settings:
  - worldgen_customization.tex / .xml    (world generation icons)
  - worldsettings_customization.tex / .xml (world settings icons)

To extract PNGs:
1. Download "ktech" (Klei texture tool) from:
   https://forums.kleientertainment.com/forums/topic/27511-ktech/

2. Convert .tex to .png:
   ktech worldgen_customization.tex worldgen_customization.png

3. Extract individual icons from the atlas using the .xml coordinates.

Alternative: Download pre-extracted icon packs from the DST modding community.

If no PNG icon is found for a key, the tool uses Unicode emoji symbols as fallback.
