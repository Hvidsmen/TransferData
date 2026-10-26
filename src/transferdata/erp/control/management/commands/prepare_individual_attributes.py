"""Separate physical-person export using the same verified attribute expansion."""
from .prepare_employee_attributes import Command as EmployeeCommand


class Command(EmployeeCommand):
    help = 'Prepare physical-person export with named additional attributes.'
    object_label = 'Физические лица'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.set_defaults(object='Справочник.ФизическиеЛица',
                            target_table='IndividualsExpanded',
                            output='.state/IndividualsExpanded.sql')
