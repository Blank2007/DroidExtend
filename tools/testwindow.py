"""Put a loud, recognisable pattern on the virtual display so a phone screenshot
can be matched against what ffmpeg captured."""
import sys, time, tkinter as tk

X, Y, W, H = 2560, 0, 1920, 1080
if len(sys.argv) > 1:
    X = int(sys.argv[1])

root = tk.Tk()
root.overrideredirect(True)
root.geometry("%dx%d+%d+%d" % (W, H, X, Y))
root.attributes("-topmost", True)
root.configure(bg="#101820")

cv = tk.Canvas(root, width=W, height=H, highlightthickness=0, bg="#101820")
cv.pack(fill="both", expand=True)

# 8x5 grid of saturated blocks: easy to check orientation and colour fidelity
cols = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff",
        "#ffffff", "#000000"]
for r in range(5):
    for c in range(8):
        x0 = c * (W // 8)
        y0 = r * (H // 5)
        cv.create_rectangle(x0, y0, x0 + W // 8, y0 + H // 5,
                            fill=cols[(r + c) % 8], outline="")

cv.create_rectangle(W // 8, H // 5, W - W // 8, H - H // 5, fill="#101820", outline="#ffffff", width=6)
cv.create_text(W // 2, H // 2 - 60, text="DROID-EXTEND", fill="#00ff88",
               font=("Consolas", 110, "bold"))
cv.create_text(W // 2, H // 2 + 60, text="VIRTUAL DISPLAY 1920x1080 @ %d" % X,
               fill="#ffffff", font=("Consolas", 44))
cv.create_text(W // 2, H - 70, text="SENTINEL-7F3A", fill="#ffcc00",
               font=("Consolas", 56, "bold"))
root.update()
root.lift()
root.focus_force()
print("test window up at %d,%d %dx%d" % (X, Y, W, H), flush=True)

if "--anim" in sys.argv:
    # full-screen motion, the worst case for a bitrate-capped link
    bar = cv.create_rectangle(0, 0, 320, H, fill="#00ff88", outline="")
    lbl = cv.create_text(200, 120, text="", fill="#ffffff", font=("Consolas", 90, "bold"))
    tick = [0]

    def step():
        tick[0] += 7
        x = (tick[0] * 9) % (W + 320) - 320
        cv.coords(bar, x, 0, x + 320, H)
        cv.itemconfigure(lbl, text="%06d" % tick[0])
        root.after(16, step)

    step()

root.mainloop()
