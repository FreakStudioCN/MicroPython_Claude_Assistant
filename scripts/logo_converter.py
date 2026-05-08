#!/usr/bin/env python3
"""
logo_converter.py —— 提取 Claude Code logo 像素坐标

从 PNG 提取橙色像素，分组为头部/眼睛/腿，输出 MicroPython 常量
"""

from PIL import Image, ImageDraw
import sys
import os

def extract_logo_data(png_path, target_size=56):
    """提取 logo 像素数据并分组"""
    img = Image.open(png_path).convert("RGBA")

    # 缩放到目标尺寸
    img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)

    # 提取橙色像素（alpha > 128 且非白色）
    pixels = []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = img.getpixel((x, y))
            # 橙色范围：R > 180, G < 150, B < 120, alpha > 128
            if a > 128 and r > 180 and g < 150 and b < 120:
                pixels.append((x, y))

    if not pixels:
        print("ERROR: 未检测到橙色像素，请检查图片")
        sys.exit(1)

    # 分层：头部 → 手臂 → 躯干 → 腿
    # 按 y 坐标统计每行的 x 范围
    rows = {}
    for x, y in pixels:
        if y not in rows:
            rows[y] = []
        rows[y].append(x)

    # 找出全宽行（手臂层）：宽度 >= 90% 的行
    min_x = min(p[0] for p in pixels)
    max_x = max(p[0] for p in pixels)
    full_width_rows = [
        y for y in rows
        if (max(rows[y]) - min(rows[y])) >= (max_x - min_x) * 0.9
    ]

    if full_width_rows:
        arm_y_min = min(full_width_rows)
        arm_y_max = max(full_width_rows)
    else:
        arm_y_min = arm_y_max = (min(rows) + max(rows)) // 2

    # 头部：手臂上方的所有像素 → 边界框
    head_pixels = [(x, y) for x, y in pixels if y < arm_y_min]
    if head_pixels:
        hx_min = min(p[0] for p in head_pixels)
        hx_max = max(p[0] for p in head_pixels)
        hy_min = min(p[1] for p in head_pixels)
        hy_max = max(p[1] for p in head_pixels)
        head = (hx_min, hy_min, hx_max - hx_min + 1, hy_max - hy_min + 1)
    else:
        head = None

    # 手臂像素 → 边界框
    arm_pixels = [(x, y) for x, y in pixels if arm_y_min <= y <= arm_y_max]
    if arm_pixels:
        ax_min = min(p[0] for p in arm_pixels)
        ax_max = max(p[0] for p in arm_pixels)
        arms = (ax_min, arm_y_min, ax_max - ax_min + 1, arm_y_max - arm_y_min + 1)
    else:
        arms = None

    # 手臂下方：按行计算"连续分组数"区分躯干 vs 腿
    def count_groups(xs, gap=3):
        if not xs:
            return 0
        xs = sorted(xs)
        g = 1
        for i in range(1, len(xs)):
            if xs[i] - xs[i-1] > gap:
                g += 1
        return g

    torso_rows = []
    leg_start_y = None
    for y in sorted(rows.keys()):
        if y <= arm_y_max:
            continue
        groups = count_groups(rows[y])
        if groups >= 3:          # 3 组以上 = 腿区域
            if leg_start_y is None:
                leg_start_y = y
        else:                    # 1-2 组 = 躯干
            if leg_start_y is None:
                torso_rows.append(y)

    # 躯干边界框
    if torso_rows:
        torso_pixels = [(x, y) for x, y in pixels if y in set(torso_rows)]
        tx_min = min(p[0] for p in torso_pixels)
        tx_max = max(p[0] for p in torso_pixels)
        ty_min = min(torso_rows)
        ty_max = max(torso_rows)
        torso = (tx_min, ty_min, tx_max - tx_min + 1, ty_max - ty_min + 1)
    else:
        torso = None

    # 腿部像素
    leg_pixels = [(x, y) for x, y in pixels if leg_start_y and y >= leg_start_y]

    # 腿部：按列分组，用像素密度检测
    if leg_pixels:
        leg_y_min = min(p[1] for p in leg_pixels)
        leg_y_max = max(p[1] for p in leg_pixels)
        leg_height = leg_y_max - leg_y_min + 1

        # 统计每列的像素数
        leg_columns = {}
        for x, y in leg_pixels:
            if x not in leg_columns:
                leg_columns[x] = []
            leg_columns[x].append(y)

        # 计算每列的密度（像素数 / 总高度）
        col_density = {}
        for x, ys in leg_columns.items():
            col_density[x] = len(ys) / leg_height

        # 找出高密度列（密度 > 0.5）和低密度列
        # 必须遍历全 x 范围，使空白列（密度=0）也能触发腿分割
        x_min_col = min(leg_columns.keys())
        x_max_col = max(leg_columns.keys())
        legs = []
        current_leg = []
        in_high_density = False

        for x in range(x_min_col, x_max_col + 1):
            density = col_density.get(x, 0)  # 缺失列视为 density=0
            if density > 0.5:  # 高密度列（腿的主体）
                if not in_high_density and current_leg:
                    # 从低密度转到高密度，结束上一条腿
                    leg_x_min = min(current_leg)
                    leg_x_max = max(current_leg)
                    leg_ys = []
                    for cx in current_leg:
                        leg_ys.extend(leg_columns[cx])
                    ly_min = min(leg_ys)
                    ly_max = max(leg_ys)
                    legs.append((leg_x_min, ly_min, leg_x_max - leg_x_min + 1, ly_max - ly_min + 1))
                    current_leg = []
                if x in leg_columns:
                    current_leg.append(x)
                in_high_density = True
            else:  # 低密度列（腿之间的间隙）
                if in_high_density and current_leg:
                    # 从高密度转到低密度，结束当前腿
                    leg_x_min = min(current_leg)
                    leg_x_max = max(current_leg)
                    leg_ys = []
                    for cx in current_leg:
                        leg_ys.extend(leg_columns[cx])
                    ly_min = min(leg_ys)
                    ly_max = max(leg_ys)
                    legs.append((leg_x_min, ly_min, leg_x_max - leg_x_min + 1, ly_max - ly_min + 1))
                    current_leg = []
                in_high_density = False

        # 最后一条腿
        if current_leg:
            leg_x_min = min(current_leg)
            leg_x_max = max(current_leg)
            leg_ys = []
            for cx in current_leg:
                leg_ys.extend(leg_columns[cx])
            ly_min = min(leg_ys)
            ly_max = max(leg_ys)
            legs.append((leg_x_min, ly_min, leg_x_max - leg_x_min + 1, ly_max - ly_min + 1))
    else:
        legs = []

    return {
        "head":  head,
        "arms":  arms,
        "torso": torso,
        "legs":  legs,
        "size":  target_size,
        "total_pixels": len(pixels)
    }


def generate_micropython_code(data):
    """生成 MicroPython 常量代码"""
    head  = data["head"]
    arms  = data["arms"]
    torso = data["torso"]
    legs  = data["legs"]

    code = f"""# Claude Code Logo 坐标数据（自动生成）
# 尺寸: {data["size"]}×{data["size"]}, 总像素: {data["total_pixels"]}

from micropython import const

LOGO_SIZE = const({data["size"]})

# 头部 (x, y, w, h)
_LOGO_HEAD = {head if head else "None"}

# 手臂 (x, y, w, h)
_LOGO_ARMS = {arms if arms else "None"}

# 躯干 (x, y, w, h)
_LOGO_TORSO = {torso if torso else "None"}

# 腿部 [(x, y, w, h), ...]
_LOGO_LEGS = [
"""
    for i, (x, y, w, h) in enumerate(legs):
        code += f"    ({x:2d}, {y:2d}, {w:2d}, {h:2d}),  # 腿 {i+1}\n"

    code += "]\n"
    return code


def visualize(png_path, data, out_path):
    """
    生成可视化图：左=缩放后原图，右=检测结果叠加框
    保存为 PNG 方便查看
    """
    target_size = data["size"]
    scale = 4  # 放大 4 倍便于观察像素细节

    # 左图：缩放后的原图，放大 scale 倍
    orig = Image.open(png_path).convert("RGBA")
    orig = orig.resize((target_size, target_size), Image.Resampling.LANCZOS)
    orig_big = orig.resize((target_size * scale, target_size * scale), Image.Resampling.NEAREST)

    # 右图：白底 + 橙色检测像素 + 各部件彩色边框
    detected = Image.new("RGBA", (target_size * scale, target_size * scale), (240, 240, 240, 255))
    draw_d = ImageDraw.Draw(detected)

    # 画出所有橙色像素（橙色小方块）
    for y in range(target_size):
        for x in range(target_size):
            r, g, b, a = orig.getpixel((x, y))
            if a > 128 and r > 180 and g < 150 and b < 120:
                draw_d.rectangle(
                    [x * scale, y * scale, x * scale + scale - 1, y * scale + scale - 1],
                    fill=(r, g, b, 255)
                )

    # 画各部件边框
    def draw_box(draw, rect, color, label):
        if rect is None:
            return
        x, y, w, h = rect
        x1, y1 = x * scale, y * scale
        x2, y2 = (x + w) * scale - 1, (y + h) * scale - 1
        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
        draw.text((x1 + 2, y1 + 2), label, fill=color)

    draw_box(draw_d, data["head"],  (0, 100, 255),   "HEAD")
    draw_box(draw_d, data["arms"],  (0, 200, 0),    "ARMS")
    draw_box(draw_d, data["torso"], (220, 0, 220),  "TORSO")
    for i, leg in enumerate(data["legs"]):
        draw_box(draw_d, leg, (255, 0, 0), f"L{i+1}")

    # 拼接左右
    W = target_size * scale
    combined = Image.new("RGBA", (W * 2 + 10, W), (180, 180, 180, 255))
    combined.paste(orig_big,  (0,       0))
    combined.paste(detected,  (W + 10,  0))

    combined.convert("RGB").save(out_path)
    print(f"[preview] Saved: {out_path}")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    png_path = os.path.join(root_dir, "claude-code-logo.png")

    if not os.path.exists(png_path):
        print(f"ERROR: not found: {png_path}")
        sys.exit(1)

    print(f"[1/4] reading: {png_path}")
    data = extract_logo_data(png_path, target_size=110)

    print(f"[2/4] extracted:")
    print(f"  - head:  {data['head']}")
    print(f"  - arms:  {data['arms']}")
    print(f"  - torso: {data['torso']}")
    print(f"  - legs:  {len(data['legs'])}")
    for i, leg in enumerate(data['legs']):
        print(f"    leg{i+1}: {leg}")
    print(f"  - total orange pixels: {data['total_pixels']}")

    # 可视化
    preview_path = os.path.join(root_dir, "scripts", "logo_preview.png")
    visualize(png_path, data, preview_path)

    print(f"[3/4] generating code:")
    code = generate_micropython_code(data)
    print(code)

    output_path = os.path.join(root_dir, "device", "logo_data.py")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)

    print(f"[4/4] saved: {output_path}")


if __name__ == "__main__":
    main()
