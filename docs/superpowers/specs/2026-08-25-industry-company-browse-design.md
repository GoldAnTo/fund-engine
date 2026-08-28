# 可审计的行业—公司浏览设计

**状态：用户已确认（2026-08-25）**

## 目标

第一版的研究项目仍只以 Company 为主体；Industry 只提供浏览入口。用户点击
“查看相关公司”时，系统必须返回有明确、可审计归属关系的 Company，而不是用行业名称
做模糊搜索或由模型猜测。

## 决策与边界

1. 一个 Company 可以有零到多个明确的 Industry 归属；没有归属时，行业页如实显示为空。
2. 归属关系由基础身份账本/fixture 显式提供；第一版复用既有
   `uw_object_relations` 中 `industry_exposes_company` 的有向关系，不从名称、文本或模型推断。
3. 不新增独立 Industry Research 项目，不据此生成研究结论，也不把 Industry 关系作为
   估值、价格、资料或判断的替代输入。
4. 只返回关系在浏览时点有效的 Company；每个 Company 仍按既有搜索规则展开其完整
   Security 组，且 Company 是唯一可启动研究的主操作。
5. 对关系缺失、对象 kind 错误、重复关系或时态不一致一律失败关闭；绝不回退为名称推测。

## 数据与 API

复用 `uw_object_relations` 的唯一三元组 `(parent_id, child_id, relation_type)`：Industry 是
parent，Company 是 child，relation type 固定为 `industry_exposes_company`。基础 fixture 对
该关系执行精确集合验证；本功能不新增自动归属、时态推断或独立的关系写入入口。

新增一个高层只读 API：给定 Industry object ID，返回该 Industry 以
`industry_exposes_company` 显式关联的 Company 搜索组。返回形状复用对象搜索中已有的
Company + Security 身份表达，不暴露原始来源、内部版本主键或研究底层配置。API 不提交、
不回滚调用方事务。

前端的“查看相关公司”调用该 API，在新建研究页展示返回的 Company 卡片；Industry 卡片本身
没有“开始研究”按钮。没有相关公司时，页面显示明确空状态。

## 一致性、错误与测试

- 用户从 Industry 浏览到 Company 后，点击 Company 才请求现有的 preview/initialize API。
- 任何 API 返回与请求 Industry 不一致、重复 Company/Security、或包含未知字段时，前端守卫
  拒绝响应。
- 数据库/loader 测试覆盖：relation type/kind 方向、重复关系、缺失/额外 fixture relation
  以及 fixture 精确加载。
- API/前端测试覆盖：Industry 返回多个 Company 完整组、无归属空结果、kind 错误、点击浏览的
  真实调用，以及 Industry 不能直接启动研究。

## 非目标

- 自动行业分类、基于文本或模型的归属推断。
- 独立行业研究项目、行业估值、行业资料生成。
- Geography Research。
