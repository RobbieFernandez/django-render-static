#!/usr/bin/env python
import os

from django.core import management


def main():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'test_settings'
    management.execute_from_command_line()


if __name__ == "__main__":
    main()
