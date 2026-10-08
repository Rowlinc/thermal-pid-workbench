# 发布到自己的 GitHub

项目可作为 Apache-2.0 衍生项目发布到自己的仓库。保留 LICENSE、NOTICE 和上游归属，README 说明新增功能；不要把上游代码宣称为全部原创。

1. 在 GitHub 建立自己的空仓库，例如 `thermal-pid-workbench`。
2. 使用干净源码目录，确认 `git status` 中没有本地凭据、生产 CSV、日志和运行结果。
3. 提交并关联自己的仓库地址：

```sh
git add .
git commit -m "Initial thermal PID workbench release"
git remote add origin YOUR_GITHUB_REPOSITORY_URL
git push -u origin main
```

`YOUR_GITHUB_REPOSITORY_URL` 替换为实际仓库地址。首次提交需要本机已配置 Git 身份；不要把别人的姓名或邮箱作为自己的作者。

GitHub Actions 会运行测试和构建。通过后，可以把源码 ZIP、sdist、wheel 和验证范围说明作为 v0.3.0 Release 附件。

仓库推荐描述：Configurable thermal PID identification, Z-N/SIMC comparison, guarded LLM tuning and gateway integration.
项目目前验证到软件/模拟网关阶段，README 中保留真实装置验证范围说明。开源发布不等价于设备投产认证。
