import datetime
from collections import namedtuple

from sqlalchemy import func as sa_func
from sqlalchemy.sql import and_, false, true

from CTFd.cache import cache
from CTFd.models import Challenges, Solves, Users, db
from CTFd.schemas.tags import TagSchema
from CTFd.utils import get_config
from CTFd.utils.dates import isoformat, unix_time_to_utc
from CTFd.utils.helpers.models import build_model_filters
from CTFd.utils.modes import generate_account_url, get_model
from CTFd.utils.security.signing import hmac
import hashlib

Challenge = namedtuple(
    "Challenge", ["id", "type", "name", "value", "category", "tags", "requirements"]
)


@cache.memoize(timeout=60)
def get_all_challenges(admin=False, field=None, q=None, **query_args):
    filters = build_model_filters(model=Challenges, query=q, field=field)
    chal_q = Challenges.query
    # Admins can see hidden and locked challenges in the admin view
    if admin is False:
        chal_q = chal_q.filter(
            and_(Challenges.state != "hidden", Challenges.state != "locked")
        )
    chal_q = (
        chal_q.filter_by(**query_args)
        .filter(*filters)
        .order_by(Challenges.value, Challenges.id)
    )
    tag_schema = TagSchema(view="user", many=True)

    results = []
    for c in chal_q:
        ct = Challenge(
            id=c.id,
            type=c.type,
            name=c.name,
            value=c.value,
            category=c.category,
            requirements=c.requirements,
            tags=tag_schema.dump(c.tags).data,
        )
        results.append(ct)
    return results


@cache.memoize(timeout=60)
def get_solves_for_challenge_id(challenge_id, freeze=False):
    Model = get_model()
    # Note that we specifically query for the Solves.account.name
    # attribute here because it is faster than having SQLAlchemy
    # query for the attribute directly and it's unknown what the
    # affects of changing the relationship lazy attribute would be
    solves = (
        Solves.query.add_columns(Model.name.label("account_name"))
        .join(Model, Solves.account_id == Model.id)
        .filter(
            Solves.challenge_id == challenge_id,
            Model.banned == False,
            Model.hidden == False,
        )
        .order_by(Solves.date.asc())
    )
    if freeze:
        freeze_time = get_config("freeze")
        if freeze_time:
            dt = datetime.datetime.utcfromtimestamp(freeze_time)
            solves = solves.filter(Solves.date < dt)
    results = []

    for solve in solves:
        # Seperate out the account name and the Solve object from the SQLAlchemy tuple
        solve, account_name = solve
        results.append(
            {
                "account_id": solve.account_id,
                "name": account_name,
                "date": isoformat(solve.date),
                "account_url": generate_account_url(account_id=solve.account_id),
            }
        )
    return results


@cache.memoize(timeout=60)
def get_solve_ids_for_user_id(user_id):
    user = Users.query.filter_by(id=user_id).first()
    solve_ids = (
        Solves.query.with_entities(Solves.challenge_id)
        .filter(Solves.account_id == user.account_id)
        .all()
    )
    solve_ids = {value for value, in solve_ids}
    return solve_ids


@cache.memoize(timeout=60)
def get_solve_counts_for_challenges(challenge_id=None, admin=False):
    if challenge_id is None:
        challenge_id_filter = ()
    else:
        challenge_id_filter = (Solves.challenge_id == challenge_id,)
    AccountModel = get_model()
    freeze = get_config("freeze")
    if freeze and not admin:
        freeze_cond = Solves.date < unix_time_to_utc(freeze)
    else:
        freeze_cond = true()
    exclude_solves_cond = and_(
        AccountModel.banned == false(),
        AccountModel.hidden == false(),
    )
    solves_q = (
        db.session.query(
            Solves.challenge_id,
            sa_func.count(Solves.challenge_id),
        )
        .join(AccountModel)
        .filter(*challenge_id_filter, freeze_cond, exclude_solves_cond)
        .group_by(Solves.challenge_id)
    )

    solve_counts = {}
    for chal_id, solve_count in solves_q:
        solve_counts[chal_id] = solve_count
    return solve_counts


def compute_challenge_passcode(team, challenge):
    if team is None or challenge is None:
        return '000000', 'ask-to-the-staff'

    # Given
    #   tag -> scenario:foo-bar
    # Then
    #   scenario_name -> foo-bar
    scenario_name = None
    for t in challenge.tags:
        if t and t.value and t.value.startswith('scenario:'):
            _, scenario_name = t.value.split(":", 1)
            scenario_name = scenario_name.strip()
            break

    # not an error, the challenge just doesn't require a passcode
    if scenario_name is None:
        return None, None

    scenario_name = str(scenario_name)
    team_created_at = str(team.created)
    team_id = str(team.id)

    passcode_hex = hmac(':'.join((scenario_name, team_created_at, team_id)))[:6]
    passcodechk = hashlib.md5(b":".join(s.encode("utf-8") for s in (scenario_name, passcode_hex))).hexdigest()[:6]

    public_passcode_hex = passcode_hex + passcodechk
    return passcode_hex, public_passcode_hex


def mangle(target_substr, seed):
    symbols = {
        "a" : "4",
        "e" : "3",
        "l" : "1",
        "o" : "0",
        "t" : "7",
        "s" : "5",
    }

    revsymbols = {ord(v): ord(k) for k, v in symbols.items()}

    tmp = target_substr.translate(revsymbols)
    lower = tmp.lower()
    upper = tmp.upper()


    if isinstance(seed, str):
        seed = seed.encode("utf-8")

    seedhash = hashlib.md5(seed).hexdigest()

    mangled = ''
    for i in range(len(target_substr)):
        c = lower[i]
        j = i % len(seedhash)
        s = int(seedhash[j], 16)

        if c.islower():
            options = [c, upper[i]]
            if c in symbols:
                options.append(symbols[c])

            choice = s % len(options)
            mangled += options[choice]
        else:
            mangled += c

    return mangled
