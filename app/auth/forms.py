from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, EmailField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp, ValidationError

from ..core.timeutil import age_on, today_local

USERNAME_PATTERN = r"^[A-Za-z0-9ÆØÅæøå_.-]{3,30}$"
MIN_AGE = 18
PASSWORD_MIN = 10
PASSWORD_MAX = 128


def _strip(value):
    return value.strip() if isinstance(value, str) else value


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


class RegistrationForm(FlaskForm):
    username = StringField(
        "Brugernavn",
        filters=[_strip],
        validators=[
            DataRequired("Vælg et brugernavn."),
            Regexp(USERNAME_PATTERN, message="3-30 tegn: bogstaver, tal, punktum, bindestreg eller understregning."),
        ],
    )
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
    accept_terms = BooleanField(
        "Jeg accepterer brugervilkårene og privatlivspolitikken",
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


class ActionForm(FlaskForm):
    """Tom formular til knapper, der kun skal have CSRF-beskyttelse."""
