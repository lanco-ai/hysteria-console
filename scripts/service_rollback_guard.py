"""Stop all replacement writers before restoring a monolith.

Command failures are deliberately fail-closed: the caller must not start the
old writer if a stop or the final inactive/PID checks cannot be verified.
"""


def quiesce_services(units, run):
    errors = []
    for unit in units:
        try:
            run('systemctl', 'disable', '--now', unit)
        except Exception as error:
            errors.append(f'{unit}: stop failed ({type(error).__name__})')
    # Check every unit even if a preceding stop failed; no early loop exit.
    for unit in units:
        try:
            result = run(
                'systemctl',
                'show',
                unit,
                '-p',
                'ActiveState',
                '-p',
                'SubState',
                '-p',
                'MainPID',
                '-p',
                'ControlPID',
            )
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            if (
                values.get('ActiveState') != 'inactive'
                or values.get('SubState') != 'dead'
                or values.get('MainPID') != '0'
                or values.get('ControlPID') != '0'
            ):
                errors.append(f'{unit}: process shutdown is not confirmed')
        except Exception as error:
            errors.append(f'{unit}: status check failed ({type(error).__name__})')
    if errors:
        raise RuntimeError('; '.join(errors))
