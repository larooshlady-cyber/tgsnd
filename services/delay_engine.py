import random


def human_delay(delay_min: float, delay_max: float) -> float:
    """Generate a human-like delay using a truncated normal distribution.

    Most delays cluster around the midpoint, with occasional shorter
    and longer pauses, mimicking natural human behavior.
    """
    mid = (delay_min + delay_max) / 2
    std = (delay_max - delay_min) / 4  # ~95% within range

    delay = random.gauss(mid, std)
    return max(delay_min, min(delay_max, delay))


def should_take_break(message_index: int, break_probability: float = 0.08) -> float:
    """Decide if a longer "AFK" break should happen.

    Returns additional break time in seconds, or 0.
    Probability increases slightly as more messages are sent (fatigue simulation).
    """
    adjusted_prob = break_probability * (1 + message_index * 0.005)
    adjusted_prob = min(adjusted_prob, 0.25)  # cap at 25%

    if random.random() < adjusted_prob:
        return random.uniform(30, 300)
    return 0
