# 工作台样式更新

采用已确认的暖白、墨蓝、冰蓝方案。用户管理维持原有五列：用户、趋势、用量、操作、链接；所有表单、权限判断、接口与数据绑定不变。

## 本次范围

- 管理后台共用侧边栏、顶部栏、卡片、指标、表单、表格和弹窗样式。
- 桌面内容区放宽至 1800px，给完整用户操作留出空间；手机保留原卡片化表格和抽屉导航。
- 用户面板用量区、订阅区、套餐设备、趋势及安全区；桌面订阅与套餐并列，窄屏堆叠。
- CSS 仅作用于 `body.has-shell` 和 `.wrap.user-panel`，不替换首页、登录页样式。
- 没有将样板中的虚拟统计或演示链接放到线上；没有新增业务功能。

## 检查与部署

- 57 项 Python 回归通过；有 2 条既存 `datetime.utcnow()` 弃用警告。
- `tests/workspace_preview_server.py` 提供本地只读、隔离数据的实际页面。
- `tests/workspace_visual.cjs` 检查五列、七项用户操作、编辑弹窗、侧边栏折叠、1920/1024/390px 无页面横向溢出及桌面用户面板并列布局。
- 浏览器覆盖总览、用户面板、流量分析、设置、模板与规则。其余管理页采用公共样式，未逐页进行浏览器交互验收。
- 仅部署 `admin.css`，并重启 `hysteria-subscription.service`。HTTPS 静态资源已校验与源码逐字节一致，带版本查询参数。
- 旧样式备份：`/root/hysteria/backups/workspace-style.NSqXLq/admin.css`。

复测：先运行 `python3 tests/workspace_preview_server.py`，再在另一个终端使用已安装的 Playwright 执行 `PLAYWRIGHT_MODULE=/tmp/hy2-login-browser/node_modules/playwright node tests/workspace_visual.cjs`。
