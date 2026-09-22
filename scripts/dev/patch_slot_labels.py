from pathlib import Path

root = Path(r"D:\PycharmProjects\trading-strategy-system-wt-fix")
p = root / "static/index.html"
t = p.read_text(encoding="utf-8")
old = """              <select id="watch-slot">
                <option value="morning">早盘</option>
                <option value="midday">午盘</option>
                <option value="evening">收盘</option>
              </select>"""
new = """              <select id="watch-slot">
                <option value="morning">早盘 09:20</option>
                <option value="midday">午盘 12:30</option>
                <option value="evening">晚盘 17:30</option>
              </select>"""
if old in t:
    p.write_text(t.replace(old, new, 1), encoding="utf-8")
    print("html ok")
else:
    # 宽松匹配
    if "早盘 09:20" in t:
        print("html already")
    else:
        t2 = t.replace(">早盘</option>", ">早盘 09:20</option>")
        t2 = t2.replace(">午盘</option>", ">午盘 12:30</option>")
        t2 = t2.replace(">收盘</option>", ">晚盘 17:30</option>")
        t2 = t2.replace(">收盘后</option>", ">晚盘 17:30</option>")
        p.write_text(t2, encoding="utf-8")
        print("html loose", "早盘 09:20" in t2)

# 描述文案
t = p.read_text(encoding="utf-8")
t = t.replace("早盘 / 午盘 / 收盘", "早盘 09:20 / 午盘 12:30 / 晚盘 17:30")
p.write_text(t, encoding="utf-8")
print("desc done")
