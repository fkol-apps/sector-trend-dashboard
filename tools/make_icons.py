"""アプリアイコン「Trends」を生成する。

デザインは 3×3 のヒートマップ（アプリのセクターカードそのもの）。
左上ほど明るい青＝強い、右下の1枚だけオレンジ＝弱い、という盤面の配色をそのまま使う。

    .venv/Scripts/python.exe tools/make_icons.py

生成物は docs/icons/ と docs/favicon.ico。デザインを変えたいときはこのファイルの
定数だけ触れば全サイズが作り直せる。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "icons"

# 512 基準の設計値（実際は SS 倍で描いてから縮小する）
BASE = 512
SS = 4                      # スーパーサンプリング倍率。角丸をなめらかにするため
MARGIN = 62                 # 外周の余白
GAP = 20                    # セル間の隙間
RADIUS = 18                 # セルの角丸
CELL = (BASE - MARGIN * 2 - GAP * 2) / 3

BG_TOP_LEFT = (19, 34, 52)      # #132234
BG_BOTTOM_RIGHT = (10, 20, 32)  # #0a1420

# 弱い → 強い の青。盤面の --pos 系と同じ方向の色。
HEAT_STOPS = [
    (26, 58, 88),
    (23, 92, 148),
    (40, 138, 200),
    (90, 180, 235),
]
WEAK_ORANGE = (224, 123, 40)    # #e07b28 盤面の --neg 相当


def heat(t: float) -> tuple[int, int, int]:
    """強さ t（0=弱い, 1=強い）を青の階調に変換する。"""
    t = max(0.0, min(1.0, t))
    span = len(HEAT_STOPS) - 1
    i = min(int(t * span), span - 1)
    f = t * span - i
    a, b = HEAT_STOPS[i], HEAT_STOPS[i + 1]
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


def gradient_background(size: int) -> Image.Image:
    """左上から右下への斜めグラデーション。透過は使わない（iOSが扱えないため）。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(
                round(BG_TOP_LEFT[k] + (BG_BOTTOM_RIGHT[k] - BG_TOP_LEFT[k]) * t)
                for k in range(3)
            )
    return img


def render(size: int, content_scale: float = 1.0) -> Image.Image:
    """アイコンを1枚描く。

    content_scale < 1 にすると格子だけを中央に縮める（Androidのマスカブル用の安全余白）。
    """
    big = size * SS
    img = gradient_background(big)
    draw = ImageDraw.Draw(img)

    k = big / BASE
    cell, gap, radius = CELL * k, GAP * k, RADIUS * k
    grid = cell * 3 + gap * 2

    grid *= content_scale
    cell *= content_scale
    gap *= content_scale
    radius *= content_scale
    origin = (big - grid) / 2

    for row in range(3):
        for col in range(3):
            # 右下の1枚だけを「弱い」オレンジにして、盤面の意味と揃える
            color = WEAK_ORANGE if (row, col) == (2, 2) else heat(1 - (row + col) / 4)
            x0 = origin + col * (cell + gap)
            y0 = origin + row * (cell + gap)
            draw.rounded_rectangle(
                [x0, y0, x0 + cell, y0 + cell], radius=radius, fill=color
            )

    return img.resize((size, size), Image.LANCZOS)


def svg_markup() -> str:
    """デスクトップのタブ用に、同じデザインのSVGも書き出す。"""
    cells = []
    for row in range(3):
        for col in range(3):
            color = WEAK_ORANGE if (row, col) == (2, 2) else heat(1 - (row + col) / 4)
            x = MARGIN + col * (CELL + GAP)
            y = MARGIN + row * (CELL + GAP)
            cells.append(
                f'  <rect x="{x:.0f}" y="{y:.0f}" width="{CELL:.0f}" height="{CELL:.0f}" '
                f'rx="{RADIUS}" fill="rgb({color[0]},{color[1]},{color[2]})"/>'
            )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BASE} {BASE}">\n'
        '  <defs>\n'
        '    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">\n'
        f'      <stop offset="0" stop-color="rgb{BG_TOP_LEFT}"/>\n'
        f'      <stop offset="1" stop-color="rgb{BG_BOTTOM_RIGHT}"/>\n'
        '    </linearGradient>\n'
        '  </defs>\n'
        f'  <rect width="{BASE}" height="{BASE}" fill="url(#bg)"/>\n'
        + "\n".join(cells)
        + "\n</svg>\n"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    targets = [
        ("apple-touch-icon.png", 180, 1.0),   # iOS ホーム画面
        ("icon-192.png", 192, 1.0),           # Android / manifest
        ("icon-512.png", 512, 1.0),           # manifest（スプラッシュ等）
        ("icon-maskable-512.png", 512, 0.72), # Android maskable（安全余白を確保）
    ]
    for name, size, scale in targets:
        img = render(size, scale)
        img.save(OUT / name, "PNG", optimize=True)
        print(f"  {name}: {size}x{size} ({(OUT / name).stat().st_size / 1024:.1f} KB)")

    # favicon は複数サイズを1ファイルに詰める
    ico = render(256)
    ico_path = ROOT / "docs" / "favicon.ico"
    ico.save(ico_path, "ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print(f"  favicon.ico: ({ico_path.stat().st_size / 1024:.1f} KB)")

    svg_path = OUT / "icon.svg"
    svg_path.write_text(svg_markup(), encoding="utf-8")
    print(f"  icon.svg: ({svg_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
