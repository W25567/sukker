"""Samler alle modeller, så de er registreret hos SQLAlchemy og Alembic."""
from .auth.models import EmailToken, User, UserSession  # noqa: F401
from .core.settings import Setting  # noqa: F401
from .location.models import Kommune, KommuneDistance, Postcode, PostcodeKommune  # noqa: F401
from .matches.models import Like, Match, MatchEvent  # noqa: F401
from .media.models import ProfileImage  # noqa: F401
from .messaging.models import Conversation, ConversationMember, Message, MessageDeletion  # noqa: F401
from .profiles.models import Profile  # noqa: F401
from .subscriptions.models import Subscription  # noqa: F401
