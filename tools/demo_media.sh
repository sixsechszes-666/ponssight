#!/usr/bin/env bash
# The demo GIF and the demo video, cut from the stills in docs/shots/.
#
#     python server.py              # http://127.0.0.1:8787
#     bash tools/demo_shots.sh      # writes docs/shots/*.png
#     bash tools/demo_media.sh      # writes docs/demo.gif and docs/demo.mp4
#
# This half does not care which base the stills came from - against a live index
# or against the seeded demo, eleven PNGs are eleven PNGs. The choice is made
# once, in demo_shots.sh.
#
# The order is defined here rather than taken from the directory listing, so
# the tour reads as one pass through the product: the board, one token opened,
# the volume board, what the indexer caught, who is sniping, one sniper, a
# handle looked up, a copy plan being written, a launch of your own, one
# watched wallet, and the watchlist those wallets sit in.
#
# Both files come from the same intermediate video, so the GIF and the MP4 are
# the same tour and cannot drift apart. The video is the honest artefact - a
# GIF cannot carry sound, cannot be scrubbed and cannot be paused, and GitHub
# renders an MP4 in a README as a player.
set -euo pipefail

RAW="${RAW:-docs/shots}"
HOLD="${HOLD:-1.6}"          # seconds each tab stays on screen
GIF_W="${GIF_W:-1000}"       # the GIF is half size; the stills underneath it are not
GIF_FPS="${GIF_FPS:-10}"
GIF_COLORS="${GIF_COLORS:-128}"
MP4_CRF="${MP4_CRF:-20}"

ORDER=(
  launches   # the board, first thing anyone sees
  coin       # one token opened, curve and trades
  volume     # volume over time
  sniped     # what the indexer caught at the block
  snipers    # those same buys, folded per wallet
  sniper     # one sniper, straight from the snipers tab
  handles    # a handle looked up, and the verdict on it
  copy       # a copy plan, with the economics the factory will charge
  create     # a launch of your own
  wallet     # one watched wallet
  wallets    # the watchlist those wallets sit in
)

for f in "${ORDER[@]}"; do
  [ -f "$RAW/$f.png" ] || { echo "missing $RAW/$f.png - run tools/demo_shots.sh" >&2; exit 1; }
done

LIST="$RAW/_frames.txt"
: > "$LIST"
for f in "${ORDER[@]}"; do
  printf "file '%s.png'\nduration %s\n" "$f" "$HOLD" >> "$LIST"
done
# The concat demuxer ignores the duration of the last entry unless the file is
# named once more without one; without this the final tab is a single frame and
# the GIF loops out of it before anyone has read it.
printf "file '%s.png'\n" "${ORDER[${#ORDER[@]}-1]}" >> "$LIST"

ffmpeg -y -loglevel error -stats \
  -f concat -safe 0 -i "$LIST" \
  -vf "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p" \
  -c:v libx264 -crf "$MP4_CRF" -preset slow -movflags +faststart \
  docs/demo.mp4

# palettegen reads the whole clip, so the palette is built from every tab and
# not from whichever one happened to be first. dither=none because ordered
# dithering on flat dark panels puts crosshatch noise over text that then reads
# as a rendering fault rather than as a compression artefact.
ffmpeg -y -loglevel error -stats \
  -i docs/demo.mp4 \
  -vf "fps=$GIF_FPS,scale=$GIF_W:-1:flags=lanczos,split[a][b];\
[a]palettegen=max_colors=$GIF_COLORS:stats_mode=diff[p];\
[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  -loop 0 docs/demo.gif

rm -f "$LIST"
for f in docs/demo.gif docs/demo.mp4; do
  printf '%-16s %s\n' "$f" "$(du -h "$f" | cut -f1)"
done
