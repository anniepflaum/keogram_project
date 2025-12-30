# Raw data/images
DSCOVR data: https://www.ngdc.noaa.gov/dscovr/portal/index.html#/download/1763510400000;1763855999999/mg1 <br \>
GOES-18 data: https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes18/l1b/mag-l1b-flat/ <br \>
keograms: https://optics.gi.alaska.edu/amisr_archive/Processed_data/aurorax/stream2/ <br \>
allsky videos: https://optics.gi.alaska.edu/realtime/data/MPEG/PKR_DASC_512/ <br \>

```bash
~/Documents/keogram_project/
├──interactive_stacks/               # contains necessary files to create keogram_YYYYMM.html within YYYYMM folders
│  └──YYYYMM/
│     ├──stacked_keograms_YYYYMM.png   # output from stack_keograms.py (year/stacked_keograms_YYYYMM.png)
│     ├──keogram_YYYYMM.html           # currently created by duplicating similar .html and adjusting for appropriate YYYYMM
│     ├──keogram_meta_YYYYMM.json      # output from build_keogram_meta.py
│     └──video_meta_YYYYMM.json        # output from build_video_meta.py
├──overlaid_full/                    # outputs from create_keogram_plots.py (full)
├──overlaid_partial/                 # outputs from create_keogram_plots.py (partial)
├──stacked_by_month/                 # outputs from stack_keograms.py (year/stacked_keograms_YYYYMM.png)
└──scripts/                          # the Python scripts (in Git)
   ├──requirements.txt                # must install before attempting to run any scripts
   ├──create_keogram_plots.py         # overlays keograms with GOES and DSCOVR data, either range of dates or range of hours
   ├──stack_keograms.py               # stacks all keograms from requested month verticaly, no overlaid data
   ├──build_keogram_meta.py           # writes json with info on each keogram within requested month
   ├──build_video_meta.py             # writes json with info on each allsky video within requested month
   ├──build_stack_html.py             # creates insteractive stack html
   └──build_interactive_stack.py      # runs 4 above scripts (build_....py) for requsted month all at once
```

# Instructions for creating an interactive stack
1. Clone git
```
git clone https://github.com/anniepflaum/keogram_project
```
2. Activate virtual environment
```
python3 -m venv .venv
source .venv/bin/activate
```
3. Upgrade pip, install requirements
```
python -m pip install --upgrade pip
pip install -r requirements.txt
```
4. Run build_interactive_stack.py
```
python3 build_interactive_stack.py
```