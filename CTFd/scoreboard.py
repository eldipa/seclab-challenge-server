from flask import Blueprint, render_template

from CTFd.utils import config
from CTFd.utils.config.visibility import scores_visible
from CTFd.utils.decorators.visibility import (
    check_account_visibility,
    check_score_visibility,
)
from CTFd.utils.decorators import authed_only
from CTFd.utils.helpers import get_infos
from CTFd.utils.scores import get_standings
from CTFd.utils.user import is_admin

scoreboard = Blueprint("scoreboard", __name__)


# We disable this --> @check_account_visibility
# so we can call this view even if Account Visibility is set to 'admin'
@scoreboard.route("/scoreboard")
@authed_only
@check_score_visibility
def listing():
    infos = get_infos()

    if config.is_scoreboard_frozen():
        infos.append("Scoreboard has been frozen")

    if is_admin() is True and scores_visible() is False:
        infos.append("Scores are not currently visible to users")

    standings = get_standings()
    return render_template("scoreboard.html", standings=standings, infos=infos)
