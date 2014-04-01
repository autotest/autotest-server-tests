import sys
import os
import commands
import logging
import random
from autotest.server import autotest_remote, hosts, subcommand, test
from autotest.client.shared import error
# pylint: disable=E0611
from autotest.client.tests.virt.virttest import utils_misc, cartesian_config
# pylint: disable=E0611
from autotest.client.tests.virt.virttest import bootstrap, utils_params
from autotest.client.tests.virt.virttest import data_dir


def generate_mac_address():
    r = random.SystemRandom()
    mac = "9a:%02x:%02x:%02x:%02x:%02x" % (r.randint(0x00, 0xff),
                                           r.randint(0x00, 0xff),
                                           r.randint(0x00, 0xff),
                                           r.randint(0x00, 0xff),
                                           r.randint(0x00, 0xff))
    return mac


class Machines(object):

    def __init__(self, host):
        self.host = host
        self.at = None
        self.params = None
        self.control = None


class multihost_migration(test.test):
    version = 2

    def run_once(self, machines, extra_params, cycles):
        VIRT_TYPE = 'qemu'
        VIRT_DIR = data_dir.get_root_dir()
        TEST_DIR = data_dir.get_backend_dir(VIRT_TYPE)
        PROV_DIR = data_dir.get_test_provider_dir('io-github-autotest-qemu')
        SHARED_DIR = os.path.join(VIRT_DIR, 'shared')
        PROV_DIR = os.path.join(PROV_DIR, VIRT_TYPE)

        bootstrap.create_config_files(TEST_DIR, SHARED_DIR, interactive=False)
        bootstrap.create_config_files(TEST_DIR, PROV_DIR, interactive=False)
        bootstrap.create_subtests_cfg(VIRT_TYPE)
        bootstrap.create_guest_os_cfg(VIRT_TYPE)

        sys.path.insert(0, VIRT_DIR)

        CONTROL_MAIN_PART = """
testname = "virt"
bindir = os.path.join(job.testdir, testname)
job.install_pkg(testname, 'test', bindir)

qemu_test_dir = os.path.join(os.environ['AUTODIR'],'tests', 'virt')
sys.path.append(qemu_test_dir)
"""
        logging.info("QEMU test running on hosts %s\n", machines)

        _hosts = {}
        for machine in machines:
            _hosts[machine] = Machines(hosts.create_host(machine))

        for host in _hosts.itervalues():
            host.at = autotest_remote.Autotest(host.host)

        cfg_file = os.path.join(TEST_DIR, "cfg", "multi-host-tests.cfg")

        if not os.path.exists(cfg_file):
            raise error.JobError("Config file %s was not found", cfg_file)

        # Get test set (dictionary list) from the configuration file
        parser = cartesian_config.Parser()
        parser.parse_file(cfg_file)
        parser.parse_string(extra_params)
        test_dicts = parser.get_dicts()

        ips = []
        for machine in machines:
            host = _hosts[machine]
            ips.append(host.host.ip)

        logging.info("")
        for i, params in enumerate(test_dicts):
            logging.info("Test    %d:  %s" % (i, params.get("shortname")))
        logging.info("")

        test_dicts = parser.get_dicts()

        test_dicts_ar = [x for x in map(lambda x: utils_params.Params(x), test_dicts)]

        if not test_dicts_ar:
            error.TestNAError("Impossible start any test with"
                              "this configuration.")

        for params in test_dicts_ar:

            params['hosts'] = ips

            for vm in params.get("vms").split():
                for nic in params.get('nics', "").split():
                    params['mac_%s_%s' % (nic, vm)] = generate_mac_address()

            params['master_images_clone'] = "image1"
            params['kill_vm'] = "yes"

            s_host = _hosts[machines[0]]
            s_host.params = params.object_params("host1")
            s_host.params['clone_master'] = "yes"
            s_host.params['hostid'] = machines[0]

            for host_id, machine in enumerate(machines[1:]):
                host = _hosts[machine]
                host.params = params.object_params("host%s" % (host_id + 2))
                params['not_preprocess'] = "yes"
                host.params['clone_master'] = "no"
                host.params['hostid'] = machine

            # Report the parameters we've received
            logging.debug("Test parameters:")
            keys = params.keys()
            keys.sort()
            for key in keys:
                logging.debug("    %s = %s", key, params[key])

            for machine in machines:
                host = _hosts[machine]
                host.control = CONTROL_MAIN_PART

            for machine in machines:
                host = _hosts[machine]
                host.control += ("job.run_test('virt', tag='%s', params=%s)" %
                                 (host.params['shortname'], host.params))

            logging.debug('Master control file:\n%s', _hosts[machines[0]].control)
            for machine in machines[1:]:
                host = _hosts[machine]
                logging.debug('Slave control file:\n%s', host.control)

            commands = []

            for machine in machines:
                host = _hosts[machine]
                commands.append(subcommand.subcommand(host.at.run,
                                                      [host.control, host.host.hostname]))

            try:
                subcommand.parallel(commands)
            except error.AutoservError, e:
                logging.error(e)
