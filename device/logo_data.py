# Claude Code Logo 坐标数据（自动生成）
# 尺寸: 110×110, 总像素: 5096

from micropython import const

LOGO_SIZE = const(110)

# 头部 (x, y, w, h)
_LOGO_HEAD = (14, 23, 82, 27)

# 眼睛 [(x, y, w, h), ...]  透明镂空，需用背景色绘制
_LOGO_EYES = [
    (27, 37,  6, 13),  # 左眼
    (75, 37,  6, 13),  # 右眼
]

# 手臂 (x, y, w, h)
_LOGO_ARMS = (0, 50, 110, 14)

# 躯干 (x, y, w, h)
_LOGO_TORSO = (13, 64, 84, 14)

# 腿部 [(x, y, w, h), ...]
_LOGO_LEGS = [
    (21, 78,  6, 14),  # 腿 1
    (34, 78,  6, 14),  # 腿 2
    (70, 78,  6, 14),  # 腿 3
    (83, 78,  6, 14),  # 腿 4
]
