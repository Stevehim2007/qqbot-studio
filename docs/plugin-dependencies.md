# 插件运行依赖声明与安装

插件可在根目录提供 `runtime_requirements.json`，声明运行所需依赖。声明只用于检测和展示，不会在安装插件时自动执行任意代码。

```json
{
  "python": [
    {"name": "Pillow", "spec": ">=10", "module": "PIL", "description": "天气图片渲染"}
  ],
  "system": [
    {"kind": "font", "target": "Noto Sans CJK SC", "description": "中文字体"}
  ]
}
```

- `python[].name/spec/module`：Python 包名、版本约束和 import 名；`module` 可省略，默认把 `-` 替换为 `_`。
- `system[].kind`：`font` / `command` / `package`；`command` 只能是单个命令名，不接受 shell 参数。
- 最多各 20 条；名称、版本约束、模块名和系统目标都有白名单字符校验。
- 插件市场预览会在 AstrBot 容器内检查缺失项；用户确认后只自动执行 `pip install` 安装缺失的 Python 包。
- 系统依赖只返回 `manual_required`，不会自动执行 `apt`。
- 依赖安装使用同一个预览 token，安装成功后才继续安装插件；用户拒绝或依赖仍缺失时取消插件安装。
- 不允许在 manifest 中声明任意命令、URL 或脚本；执行入口固定。

当前 `qqbot_weather` 声明 `Pillow >=10` 和 `Noto Sans CJK SC`。
