from typing import Optional
from datamaestro.definitions import AbstractDataset, hook


@hook("pre-use")
def useragreement(definition: AbstractDataset, message: str, id: Optional[str] = None):
    """Asks for a user-agreement

    :param definition: The dataset for which the agreement is asked
    :param message: The agreement text
    :param id: The ID of the agreement (default to the dataset ID)
    """
    # Skip agreement when testing
    if definition.context.running_test:
        return

    settings = definition.context.user_settings
    id = id or definition.id
    answer = settings.agreements.get(id, "no")
    if answer == "yes":
        return

    answer = definition.context.ask(
        message + "\nanswer [yes/no] ",
        {"yes": "yes", "y": "yes", "no": "no", "n": "no"},
    )

    if answer == "yes":
        settings.agreements[id] = "yes"
        settings.save()
    else:
        raise ValueError("Agreement not accepted")
