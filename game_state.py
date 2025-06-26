# game_state.py
from collections import defaultdict
from datetime import datetime
from db import execute_query

class GameState:
    def __init__(self, round_counter_per_day):
        self.round_counter_per_day = round_counter_per_day
        self.round_id = None
        self.bets = defaultdict(list)
        self.is_betting_open = False
        self.winning_w = None
        self.winning_t = []

    def start_new_round(self, group_id):
        now = datetime.now()
        today_str = now.strftime('%y%m%d')

        # 查询数据库中今天的最大局号
        result = execute_query(
            "SELECT round_id FROM bets_2d WHERE group_id = %s AND round_id LIKE %s ORDER BY created_at DESC LIMIT 1",
            (group_id, f"{today_str}%")
        )

        if result:
            last_round_id = result[0][0]  # 例如 "250625A03"
            last_batch = last_round_id[6]      # A
            last_serial = int(last_round_id[7:])  # 03
            batch_index = ord(last_batch) - ord('A')
            count = batch_index * 99 + last_serial + 1
        else:
            count = 1  # 今天第一局

        # 转换为字母+编号
        batch_letter = chr(ord('A') + (count - 1) // 99)  # A-Z
        serial_number = (count - 1) % 99 + 1              # 1–99

        # 构建新的 round_id
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
