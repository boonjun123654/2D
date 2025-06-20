from collections import defaultdict
from datetime import datetime

class GameState:
    def __init__(self):
        self.round_number = 1
        self.reset()

    def reset(self):
        self.bets = defaultdict(list)
        self.total_bets = defaultdict(int)
        self.locked = False
        self.round_id = self.generate_round_id()

    def generate_round_id(self):
        now = datetime.now()
        date_str = now.strftime("%y%m%d")  # e.g. 250620
        return f"2D{date_str}{self.round_number:03d}"

    def next_round(self):
        self.round_number += 1
        self.round_id = self.generate_round_id()
        self.locked = False
        self.bets.clear()
        self.total_bets.clear()

    def add_bet(self, user_id, number, amount):
        if self.locked:
            return False
        self.bets[user_id].append((number, amount))
        self.total_bets[number] += amount
        return True

    def lock(self):
        self.locked = True

    def get_all_bets(self):
        return self.bets

    def get_round_id(self):
        return self.round_id

    def is_locked(self):
        return self.locked

    def set_winning_number(self, number):
        self.winning_number = number

    def get_winners(self):
        winners = []
        for uid, bets in self.bets.items():
            for num, _ in bets:
                if str(num).zfill(2) == self.winning_number:
                    winners.append(uid)
                    break
        return winners

state = GameState()
