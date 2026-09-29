// Chinese renderings of the metrics engine's generated basis / caveat sentences. Numbers and names inside
// them are kept verbatim (captured groups); anything unmatched is shown in English rather than guessed.
const RULES: [RegExp, string][] = [
  [/^badge data: listings without a badge sell < first rung; estimate from the interval-censored demand model$/, "徽章数据：没有徽章的商品销量低于第一档；由区间删失需求模型估算"],
  [/^exact sales where present; missing sales modelled$/, "有精确销量时直接使用；缺失销量由模型估算"],
  [/^brand shares of estimated revenue$/, "按估算销售额计算的品牌份额"],
  [/^certain lower bound$/, "确定下限"],
  [/^estimate$/, "估计值"],
  [/^estimate \(joint simulation\)$/, "估计值（联合模拟）"],
  [/^sum of observation lower bounds \(certain\)$/, "观测区间下限之和（确定）"],
  [/^sum of observation upper bounds$/, "观测区间上限之和"],
  [/^(.+) \/ HHI$/, "$1 / HHI"],
  [/^top brand: (.+)$/, "头部品牌：$1"],
  [/^entrant units \(segment and market entrants\) x prices of the segment's listings \(no genuine unit costs in source\)$/, "新品销量（细分及市场新品）× 细分商品价格（数据源无真实单位成本）"],
  [/^entrant units \(segment entrants\) x prices of the segment's listings \(no genuine unit costs in source\)$/, "新品销量（细分新品）× 细分商品价格（数据源无真实单位成本）"],
  [/^entrants \(launched <= (\d+) days before (.+)\) reaching the median estimated units of established listings$/, "达到成熟商品估算销量中位数的新品（在 $2 之前 $1 天内上架）"],
  [/^estimated units sold by listings rated < ([\d.]+)$/, "评分低于 $1 的商品的估算销量"],
  [/^few entrants -- wide uncertainty$/, "新品较少——不确定性大"],
  [/^listings launched within (\d+) days$/, "$1 天内上架的商品"],
  [/^listings selling below the first badge rung$/, "销量低于第一档徽章的商品"],
  [/^median rating of the top ([\d.]+%?) listings by estimated revenue$/, "按估算销售额排名前 $1 商品的中位评分"],
  [/^no entrants in the segment: market entrant rate used$/, "该细分没有新品：使用市场新品成功率"],
  [/^no valid unit cost in the source$/, "数据源中没有有效的单位成本"],
  [/^price x \(1 - (.+)\) - fulfilment fee - unit cost$/, "价格 ×（1 − $1）− 配送费 − 单位成本"],
  [/^rating-based proxy; review text not in source$/, "基于评分的代理指标；数据源没有评论文本"],
  [/^segment and market entrants$/, "细分及市场新品"],
  [/^segment entrants$/, "细分新品"],
  [/^segment entrants shrunk toward the market rate$/, "细分新品（向市场比率收缩）"],
  [/^share of simulated entrants reaching (.+) USD\/month (revenue|profit)$/, "模拟新品中月$2达到 $1 美元的比例"],
  [/^(\w+) -> ([\d.]+) \(x0=(.+) x1=(.+)\)$/, "$1 → $2（x0=$3，x1=$4）"],
  [/^no gap analysis for this sub-category \(too few listings\): concept priced at its median$/, "该子类目没有缺口分析（商品太少）：概念按其中位价格定价"],
  [/^(\d+) existing listings with all recommended features$/, "$1 个现有商品具备全部推荐功能"],
  [/^(\d+) no significant feature gap -- listings in the best price band$/, "没有显著的功能缺口——最佳价格带中的 $1 个商品"],
  // product data-confidence reasons (intelligence/confidence.py)
  [/^(.+) source \(reliability (\d+%)\)$/, "$1 数据源（可靠性 $2）"],
  [/^sales observed on (\d+) of (\d+) listing\(s\)$/, "$2 个商品页中有 $1 个观测到销量"],
  [/^no observed sales — demand unverified$/, "没有观测到销量——需求未经验证"],
  [/^(\d+) listings corroborate this product$/, "$1 个商品页相互印证此产品"],
  [/^single listing$/, "仅一个商品页"],
  [/^(\d+%) of key fields present$/, "关键字段完整度 $1"],
  [/^passes all quality checks$/, "通过全部质量检查"],
  [/^(\d+%) of listings carry quality flags$/, "$1 的商品页带有质量标记"],
  [/^relevance confidence (\d+%)$/, "相关性置信度 $1"],
  [/^([\d,]+) reviews$/, "$1 条评论"],
  [/^rating present but review count not in source$/, "有评分但数据源中没有评论数"],
  [/^no rating$/, "没有评分"],
  [/^sales across (\d+) snapshots vary (\d+%) \(consistent\)$/, "$1 个快照间销量波动 $2（稳定）"],
  [/^sales across (\d+) snapshots vary (\d+%) \(volatile\)$/, "$1 个快照间销量波动 $2（不稳定）"],
  [/^one snapshot — consistency measurable after the next upload$/, "仅一个快照——下次上传后可衡量一致性"],
];

export function basisZh(text: string): string {
  for (const [re, zh] of RULES) {
    if (re.test(text)) return text.replace(re, zh).replace("revenue", "销售额").replace("profit", "利润");
  }
  return text;
}
