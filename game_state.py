# game_state.py
from collections import defaultdict
from datetime import datetime

class GameState:
    def __init__(self):
        self.round_id = None
        self.bets = defaultdict(list)  # number -> total amount
        self.is_betting_open = False
        self.winning_w = None  # 单个头奖号码
        self.winning_t = []    # 特别奖号码列表

    def start_new_round(self):
        now = datetime.now()
        today_str = now.strftime('%y%m%d')

        # 增加今天的局号计数
        round_counter_per_day[today_str] += 1
        count = round_counter_per_day[today_str]

        # 计算字母和数字部分
        batch_letter = chr(ord('A') + (count - 1) // 99)  # A~Z
        serial_number = (count - 1) % 99 + 1               # 1~99

        # 格式化局号，例如：250622A01
        self.round_id = f"{today_str}{batch_letter}{serial_number:02d}"

        # 其他初始化
        self.bets.clear()
        self.is_betting_open = True
        self.winning_w = None
        self.winning_t = []

    def add_bet(self, number: int, amount: int, user_id: int, name: str):
        if self.is_betting_open:
            self.bets[number].append((user_id, name, amount))

    def lock_bets(self):
        self.is_betting_open = False

    def get_total_bets(self):
        return dict(self.bets)

    def get_top_bettor(self):
        if not self.bets:
            return None, 0
        sorted_bets = sorted(self.bets.items(), key=lambda x: x[1], reverse=True)
        return sorted_bets[0]
