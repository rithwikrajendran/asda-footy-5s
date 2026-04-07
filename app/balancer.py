"""Snake-draft team balancer for creating fair teams."""

from typing import List, Tuple


def snake_draft(players_with_ratings: List[Tuple[int, str, float]]) -> Tuple[list, list, float, float]:
    """
    Snake draft to balance teams.

    Args:
        players_with_ratings: List of (player_id, player_name, overall_rating)

    Returns:
        (team_a, team_b, team_a_avg, team_b_avg)
        Each team is a list of (player_id, player_name, overall_rating)
    """
    sorted_players = sorted(players_with_ratings, key=lambda x: x[2], reverse=True)

    team_a = []
    team_b = []

    for i, player in enumerate(sorted_players):
        # Snake: 0->A, 1->B, 2->B, 3->A, 4->A, 5->B, ...
        cycle = i // 2
        pos_in_pair = i % 2
        if cycle % 2 == 0:
            # Even cycle: first goes A, second goes B
            if pos_in_pair == 0:
                team_a.append(player)
            else:
                team_b.append(player)
        else:
            # Odd cycle: first goes B, second goes A
            if pos_in_pair == 0:
                team_b.append(player)
            else:
                team_a.append(player)

    avg_a = sum(p[2] for p in team_a) / len(team_a) if team_a else 0
    avg_b = sum(p[2] for p in team_b) / len(team_b) if team_b else 0

    return team_a, team_b, round(avg_a, 2), round(avg_b, 2)
