# The README demo: the real osx-next TUI on a fake host (no Proxmox needed).
#   vhs scripts/demo/tui.tape        (run from the repository root, writes docs/static/img/demo-tui.gif)
# The fake host and its scripted build pace live in scripts/demo/run_demo.py.

Output docs/static/img/demo-tui.gif

Set Shell "bash"
Set FontSize 14
Set Width 962
Set Height 535
Set Padding 16
Set Theme { "background": "#0b0f14", "foreground": "#e6edf3", "cursor": "#0b0f14", "black": "#0b0f14", "brightBlack": "#8b98a8", "white": "#e6edf3" }
Set TypingSpeed 120ms
Set Framerate 12
Set WindowBar ""
Set BorderRadius 0
Set Margin 0
Set MarginFill "#0b0f14"

# start the app out of sight: it takes a few seconds to load the fake desktop
Hide
Type "clear; PYTHONPATH=src python3 scripts/demo/run_demo.py"
Enter
Sleep 7s
Show

# Home: both Macs
Sleep 2.5s
Right
Sleep 800ms
Left
Sleep 800ms

# New Mac: pick another macOS and a lighter size, then create it
Type "n"
Sleep 1.2s
Right
Sleep 900ms
Down
Down
Sleep 500ms
Left
Sleep 1.2s
Enter

# Build: the timeline advances, the Mac's screen shows beside it
Sleep 7.5s
Escape
Sleep 2s

# Help for a moment
Type "?"
Sleep 1.5s
Type "?"
Sleep 400ms

# Graphics card on the first Mac
Type "g"
Sleep 3s
Type "p"
Sleep 1.2s
Down
Sleep 250ms
Down
Sleep 250ms
Down
Sleep 250ms
Down
Sleep 800ms
Escape
Sleep 700ms
Enter
Sleep 2.5s
Enter
Sleep 7s
