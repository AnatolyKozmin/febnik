from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    waiting_fio = State()


class Award(StatesGroup):
    pick_activity = State()
    enter_username = State()


class ClaimPick(StatesGroup):
    choosing_prize = State()


class BalanceRequestFlow(StatesGroup):
    enter_amount = State()
    enter_comment = State()
