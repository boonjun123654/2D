from decimal import Decimal

# “总返还倍数”（含本金）。赔付 = stake * (odds - 1)
ODDS_2D = {
"N1": Decimal("50"), # 只中头奖
"N_HEAD": Decimal("28"), # N 对头奖
"N_SPECIAL": Decimal("7"), # N 对特别奖
"B": Decimal("1.90"), # 大（看头奖）
"S": Decimal("1.90"), # 小
"DS": Decimal("1.90"), # 单
"SS": Decimal("1.90"), # 双
}
