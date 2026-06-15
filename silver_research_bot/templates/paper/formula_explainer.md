对给定论文中的每个数学公式逐条解释其具体含义。直接输出完整的 HTML 片段，禁止加入问候语、角色介绍、思考过程或元评论，禁止使用 Markdown 代码围栏包裹。

## 输出格式

每个公式输出为一个 `.frow` 卡片，按分类分组。严格按照以下 HTML 结构：

```html
<p class="sec-title">分类名称</p>

<div class="frow">
<div class="fnum">式 N</div>
<div class="fbody">
<span class="ftag tag-xxx">分类标签</span>
<div class="fexpr">公式的 LaTeX 表达式（原文符号）</div>
<div class="fmean">公式含义的简洁解释，<b>关键术语</b>用 b 标签加粗</div>
</div>
</div>
```

## 分类标签的 CSS class 对照

- 系统状态描述 / 系统模型 / 通信模型 → tag-sys
- 问题建模 / MDP定义 / MDP状态 → tag-mdp
- 奖励函数 / 奖励 → tag-rwd
- 算法核心 / 策略梯度 / 优化 → tag-alg
- GAT编码器 / 图注意力 / 神经网络 → tag-gat
- 观测优化 / 状态处理 → tag-obs
- 动力学 / 能耗模型 → tag-sys
- 其他无法归类的公式 → tag-sys

## 要求

1. 识别每个公式所属的类别，按类别分组，每组前加 `<p class="sec-title">类别名称</p>`
2. 同一类别内的公式按编号顺序排列
3. 公式 LaTeX 表达式放在 `<div class="fexpr">` 中，使用原文符号（如 p_i, R_ij, \gamma 等），不要用 LaTeX 命令包裹（不要加 $ 或 $$）
4. 含义解释放在 `<div class="fmean">` 中，关键概念用 `<b>` 加粗，每条解释 1-2 句
5. 公式编号用中文"式 N"格式
6. 每个公式必须解释，不跳过，不合并
7. 直接输出 HTML，禁止用 ```html 包裹，禁止加任何前言后语
