# 新海天主题设计

根据用户提供的角色图，将银白头发、紫色眼睛、蓝色领口与粉色领结转化为界面的视觉语言。

- 默认浅色：雾白背景、淡紫面板、冰蓝图标与樱粉点缀；夜间模式使用灰紫底色。
- 欢迎页：新海天角色立绘、细线轨道、角色名牌和任务建议。
- 角色系统：侧栏角色卡、聊天头像、星形品牌标记与温和文案。
- 工作区域：会话、工具活动、审批、文件预览和变更功能保持原有交互；未修改模型系统提示或 CLI。
- 新主题使用独立的浏览器主题偏好 `soranimi-nimi-theme`，首次默认浅色；深浅主题仍可手动切换。

## 素材

角色资源：[nimi-character.png](../src/soranimi/web/static/nimi-character.png)。1024 × 1536 RGBA PNG，透明通道已确认；欢迎页与头像使用 CSS 布局和裁切，不修改素材内容。

新增表情资源：[nimi-avatar.jpg](../src/soranimi/web/static/nimi-avatar.jpg)，使用用户提供的眨眼图片原图。用于侧栏角色卡、欢迎区问候徽章及助手消息头像，按容器比例显示；欢迎页主立绘仍使用透明 PNG。两种主题共用本地素材。

使用内置 **image_gen** 工具，以用户提供的图片为编辑参考提取角色。素材已复制到项目中，运行时不依赖临时文件、生成目录或外部网站。

最终生成提示：

> Use case: background-extraction. Asset type: transparent character cutout for a local coding-assistant UI. Edit target: the provided reference sheet. Extract ONLY the single fully clothed anime character from the reference into a genuine transparent PNG. Preserve her identity, face, purple eyes, silvery white long side ponytail, pastel blue sailor collar and pink ribbon, white uniform with navy/gray plaid trim, black knee socks and blue shoes. Keep exactly the same standing pose and complete full-body framing, every hand, hair strand, skirt and shoe within the frame. No redesign, no age change, no new pose, no new accessories. Omit all surrounding reference-sheet typography, profile paragraphs, yellow/blue headings, background, logos and decorative writing so the cutout contains only the character. Clean fine hair alpha edges, no background color and no cast-shadow rectangle. Center the full standing character on a tall transparent canvas, with small transparent padding around the whole silhouette.

## 实现文件

- `src/soranimi/web/static/nimi-theme.css`：覆盖旧主题的色板、角色布局和响应式细节。
- `src/soranimi/web/static/index.html`：角色欢迎页、角色卡与输入框。
- `src/soranimi/web/static/app.js`：角色头像和主题偏好。
- `src/soranimi/web/static/logo.svg`：紫、粉、冰蓝星形标记。

UI 仍通过 `./start-ui.ps1` 或 conda Lang 环境中的 `python -m soranimi --ui` 启动。

## 预览与验证

- [浅色欢迎页](images/nimi-workbench-light.png)
- [夜间欢迎页](images/nimi-workbench-dark.png)
- [聊天与工具记录](images/nimi-chat.png)

在 conda Lang 环境运行的 11 项 Web 集成测试通过，JavaScript 语法检查和 Ruff 检查通过。浏览器离线演示确认角色头像、消息发送与 `list_dir` 工具记录正常。
