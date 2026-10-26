from .prepare_individual_attributes import Command as IndividualCommand


class Command(IndividualCommand):
    help = 'Prepare individuals with additional attributes, all email addresses and full contact rows.'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.set_defaults(with_contacts=True, target_table='IndividualsContactsExpanded',
                            output='.state/IndividualsContactsExpanded.sql')
