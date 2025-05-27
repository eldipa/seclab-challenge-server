from collections import defaultdict

from flask_restx import Namespace, Resource
from sqlalchemy import select

from CTFd.cache import cache, make_cache_key
from CTFd.models import Awards, Brackets, Solves, Users, db
from CTFd.utils import get_config
from CTFd.utils.dates import isoformat, unix_time_to_utc
from CTFd.utils.decorators.visibility import (
    check_account_visibility,
    check_score_visibility,
)
from CTFd.utils.modes import TEAMS_MODE, generate_account_url, get_mode_as_word
from CTFd.utils.scores import get_standings, get_user_standings

scoreboard_namespace = Namespace(
    "scoreboard", description="Endpoint to retrieve scores"
)


@scoreboard_namespace.route("")
class ScoreboardList(Resource):
    @check_score_visibility
    @cache.cached(timeout=60, key_prefix=make_cache_key)
    def get(self):
        standings = get_standings()
        response = []
        mode = get_config("user_mode")
        account_type = get_mode_as_word()

        if mode == TEAMS_MODE:
            r = db.session.execute(
                select(
                    [
                        Users.id,
                        Users.name,
                        Users.oauth_id,
                        Users.team_id,
                        Users.hidden,
                        Users.banned,
                        Users.bracket_id,
                        Brackets.name.label("bracket_name"),
                    ]
                )
                .where(Users.team_id.isnot(None))
                .join(Brackets, Users.bracket_id == Brackets.id, isouter=True)
            )
            users = r.fetchall()
            membership = defaultdict(dict)
            for u in users:
                if u.hidden is False and u.banned is False:
                    membership[u.team_id][u.id] = {
                        "id": u.id,
                        "oauth_id": u.oauth_id,
                        "name": u.name,
                        "score": 0,
                        "bracket_id": u.bracket_id,
                        "bracket_name": u.bracket_name,
                    }

            # Get user_standings as a dict so that we can more quickly get member scores
            user_standings = get_user_standings()
            for u in user_standings:
                membership[u.team_id][u.user_id]["score"] = int(u.score)

        # Get the count of challenges by category
        r = db.session.execute(
                '''
                select c.category, count(distinct c.id)
                from challenges c
                group by c.category
                '''
        )

        challenges_cnt_by_category = {}
        for category_name, challenge_cnt in r:
            challenges_cnt_by_category[category_name] = int(challenge_cnt)

        # Get the count of challenges solved by category and team/user
        account_field = 'team_id' if mode == TEAMS_MODE else 'user_id'
        r = db.session.execute(
                f'''
                select s.{account_field}, c.category, count(distinct c.id)
                from solves s
                join challenges c on s.challenge_id = c.id
                group by s.{account_field}, c.category
                '''
        )
        del account_field

        category_stats_by_account_id = defaultdict(dict)
        for account_id, category_name, challenge_cnt in r:
            category_stats_by_account_id[account_id][category_name] = int(challenge_cnt)

        category_stats_by_account_id = dict(category_stats_by_account_id)

        # Compute how many categories were fully completed and how many were "almost"
        # fully completed where "almost" means >= than a certain percentage (80%)
        Q = 0.8

        stats_by_account_id = {}
        for account_id in category_stats_by_account_id.keys():
            categories_completed = 0
            categories_almost_completed = 0
            for category_name in challenges_cnt_by_category.keys():
                solved_in_cat_cnt = category_stats_by_account_id[account_id][category_name]
                challenge_in_cat_cnt = challenges_cnt_by_category[category_name]

                if solved_in_cat_cnt == challenge_in_cat_cnt:
                    categories_completed += 1
                elif solved_in_cat_cnt >= int(challenge_in_cat_cnt * Q):
                    categories_almost_completed += 1

            stats_by_account_id[account_id] = (categories_completed, categories_almost_completed)


        print("CAT", category_stats_by_account_id)
        print("STAT", stats_by_account_id)
        for i, x in enumerate(standings):
            entry = {
                "pos": i + 1,
                "account_id": x.account_id,
                "account_url": generate_account_url(account_id=x.account_id),
                "account_type": account_type,
                "oauth_id": x.oauth_id,
                "name": x.name,
                "score": int(x.score),
                "bracket_id": x.bracket_id,
                "bracket_name": x.bracket_name,
                "categories_completed": stats_by_account_id[x.account_id][0],
                "categories_almost_completed": stats_by_account_id[x.account_id][1],
            }

            if mode == TEAMS_MODE:
                entry["members"] = list(membership[x.account_id].values())
                entry["members_count"] = len(entry["members"])

            response.append(entry)

        return {"success": True, "data": response}


@scoreboard_namespace.route("/top/<int:count>")
@scoreboard_namespace.param("count", "How many top teams to return")
class ScoreboardDetail(Resource):
    @check_score_visibility
    @cache.cached(timeout=60, key_prefix=make_cache_key)
    def get(self, count):
        response = {}

        standings = get_standings(count=count)

        team_ids = [team.account_id for team in standings]

        solves = Solves.query.filter(Solves.account_id.in_(team_ids))
        awards = Awards.query.filter(Awards.account_id.in_(team_ids))

        freeze = get_config("freeze")

        if freeze:
            solves = solves.filter(Solves.date < unix_time_to_utc(freeze))
            awards = awards.filter(Awards.date < unix_time_to_utc(freeze))

        solves = solves.all()
        awards = awards.all()

        # Build a mapping of accounts to their solves and awards
        solves_mapper = defaultdict(list)
        for solve in solves:
            solves_mapper[solve.account_id].append(
                {
                    "challenge_id": solve.challenge_id,
                    "account_id": solve.account_id,
                    "team_id": solve.team_id,
                    "user_id": solve.user_id,
                    "value": solve.challenge.value,
                    "date": isoformat(solve.date),
                }
            )

        for award in awards:
            solves_mapper[award.account_id].append(
                {
                    "challenge_id": None,
                    "account_id": award.account_id,
                    "team_id": award.team_id,
                    "user_id": award.user_id,
                    "value": award.value,
                    "date": isoformat(award.date),
                }
            )

        # Sort all solves by date
        for team_id in solves_mapper:
            solves_mapper[team_id] = sorted(
                solves_mapper[team_id], key=lambda k: k["date"]
            )

        for i, _team in enumerate(team_ids):
            response[i + 1] = {
                "id": standings[i].account_id,
                "name": standings[i].name,
                "solves": solves_mapper.get(standings[i].account_id, []),
            }
        return {"success": True, "data": response}
