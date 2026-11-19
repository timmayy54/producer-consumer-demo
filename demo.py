"""Generate repeatable fictional football pages for the local HTTP demo."""

import argparse
import random
from pathlib import Path


# Familiar English clubs, not a claim about a current season's membership/fixtures.
TEAMS = ["Manchester United", "Chelsea", "Arsenal", "Liverpool", "Manchester City",
         "Tottenham Hotspur", "Everton", "Newcastle United", "Aston Villa", "Fulham"]


def generate_site(directory: Path, count: int = 100, port: int = 8000) -> None:
    if count < 1:
        raise ValueError("count must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    randomizer = random.Random(42)
    urls = []
    for index in range(count):
        home, away = randomizer.sample(TEAMS, 2)
        # Probabilities sum to 1.04; each is at least 1/3, hence odds <= 3.
        extra_home = randomizer.uniform(0, 0.04)
        extra_draw = randomizer.uniform(0, 0.04 - extra_home)
        probabilities = [1 / 3 + extra_home, 1 / 3 + extra_draw,
                         1 / 3 + 0.04 - extra_home - extra_draw]
        home_price, draw_price, away_price = [1 / probability for probability in probabilities]
        over_price, under_price = [randomizer.uniform(1.5, 3) for _ in range(2)]
        filename = f"match-{index + 1:03}.html"
        next_page = f"match-{(index + 1) % count + 1:03}.html"
        html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{home} vs {away}</title></head>
<body>
<h1>{home} vs {away}</h1>
<p>Fictional demonstration fixture. Prices are synthetic, not live.</p>
<section id="match-winner"><h2>Match winner</h2>
<ul><li>Home: {home_price:.6f}</li><li>Draw: {draw_price:.6f}</li><li>Away: {away_price:.6f}</li></ul>
</section>
<section id="total-goals"><h2>Over / under 2.5 goals</h2>
<ul><li>Over: {over_price:.2f}</li><li>Under: {under_price:.2f}</li></ul></section>
<nav><a href="{next_page}">Next match</a>
<a href="#match-winner">Match winner</a><a href="#total-goals">Total goals</a></nav>
</body></html>
'''
        (directory / filename).write_text(html, encoding="utf-8")
        urls.append(f"http://127.0.0.1:{port}/{filename}")
    (directory / "urls.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.count < 1 or not 1 <= args.port <= 65535:
        parser.error("count must be positive and port must be between 1 and 65535")
    generate_site(Path("demo_site"), args.count, args.port)
    print(f"Generated {args.count} pages in demo_site")
