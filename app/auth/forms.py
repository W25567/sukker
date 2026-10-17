from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, EmailField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp, ValidationError

from ..core.timeutil import age_on, today_local

# Vist navn: behøver ikke være unikt. Bogstaver (også æøå og accenter), tal og enkelte skilletegn.
DISPLAY_NAME_PATTERN = r"^[^\W_][\w .'\-]{0,28}[^\W_]$"  # starter og slutter med bogstav eller tal
MIN_AGE = 18
PASSWORD_MIN = 10
PASSWORD_MAX = 128


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def normalize_display_name(value):
    """Fjerner mellemrum i enderne og samler gentagne mellemrum."""
    return " ".join(value.split()) if isinstance(value, str) else value


def _lower(value):
    return value.strip().lower() if isinstance(value, str) else value


def _new_password_field(label="Password"):
    return PasswordField(
        label,
        validators=[
            DataRequired("Skriv et password."),
            Length(PASSWORD_MIN, PASSWORD_MAX, f"Passwordet skal være mellem {PASSWORD_MIN} og {PASSWORD_MAX} tegn."),
        ],
    )


def _display_name_field():
    return StringField(
        "Vist navn",
        filters=[normalize_display_name],
        validators=[
            DataRequired("Skriv det navn, andre skal se."),
            Regexp(DISPLAY_NAME_PATTERN, message="2-30 tegn: bogstaver, tal, mellemrum, punktum, bindestreg eller apostrof."),
        ],
    )


class DisplayNameForm(FlaskForm):
    """Ret det viste navn under Konto. Samme regler som ved oprettelsen."""

    display_name = _display_name_field()


class RegistrationForm(FlaskForm):
    display_name = _display_name_field()
    email = EmailField(
        "E-mail",
        filters=[_lower],
        validators=[DataRequired("Skriv din e-mail."), Email("Skriv en gyldig e-mail."), Length(max=254)],
    )
    password = _new_password_field()
    password_confirm = PasswordField(
        "Gentag password", validators=[DataRequired("Gentag passwordet."), EqualTo("password", "Passwordene er ikke ens.")]
    )
    birthdate = DateField("Fødselsdato", validators=[DataRequired("Skriv din fødselsdato.")])
    postcode = StringField(
        "Postnummer",
        filters=[_strip],
        validators=[DataRequired("Skriv dit postnummer."), Regexp(r"^\d{4}$", message="Postnummeret skal være fire cifre.")],
    )
    # Vises kun, når postnummeret dækker flere kommuner (specifikation 5). Valgene sættes i view'et.
    kommune = SelectField("Kommune", choices=[], validate_choice=False)
    # Teksten med links står i skabelonen (auth/register.html).
    accept_terms = BooleanField(
        "Jeg er fyldt 18 år og accepterer brugervilkårene og privatlivspolitikken",
        validators=[DataRequired("Du skal acceptere vilkårene for at oprette en profil.")],
    )

    def validate_birthdate(self, field):
        today = today_local()
        if field.data > today:
            raise ValidationError("Fødselsdatoen kan ikke ligge i fremtiden.")
        if age_on(field.data, today) < MIN_AGE:
            raise ValidationError("Du skal være mindst 18 år for at oprette en profil.")
        if today.year - field.data.year > 120:
            raise ValidationError("Tjek fødselsdatoen.")

    def validate_postcode(self, field):
        # Præcis kontrol mod postnummerdatabasen kommer med lokationen (M3).
        if field.data and not 800 <= int(field.data) <= 9999:
            raise ValidationError("Det ligner ikke et dansk postnummer.")


class LoginForm(FlaskForm):
    email = EmailField("E-mail", filters=[_lower], validators=[DataRequired("Skriv din e-mail.")])
    password = PasswordField("Password", validators=[DataRequired("Skriv dit password.")])


class ForgotPasswordForm(FlaskForm):
    email = EmailField(
        "E-mail", filters=[_lower], validators=[DataRequired("Skriv din e-mail."), Email("Skriv en gyldig e-mail.")]
    )


class ResetPasswordForm(FlaskForm):
    password = _new_password_field("Nyt password")
    password_confirm = PasswordField(
        "Gentag nyt password", validators=[DataRequired("Gentag passwordet."), EqualTo("password", "Passwordene er ikke ens.")]
    )


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Nuværende password", validators=[DataRequired("Skriv dit nuværende password.")])
    password = _new_password_field("Nyt password")
    password_confirm = PasswordField(
        "Gentag nyt password", validators=[DataRequired("Gentag passwordet."), EqualTo("password", "Passwordene er ikke ens.")]
    )


class DeleteAccountForm(FlaskForm):
    password = PasswordField("Dit password", validators=[DataRequired("Skriv dit password for at bekræfte.")])
    confirm = BooleanField(
        "Jeg forstår, at min profil, mine billeder, likes og matches slettes, og at det ikke kan fortrydes",
        validators=[DataRequired("Bekræft, at du vil slette din profil.")],
    )


class ActionForm(FlaskForm):
    """Tom formular til knapper, der kun skal have CSRF-beskyttelse."""
