import sys
import os
import commands
import logging
import random
import shutil
from autotest.server import autotest_remote, hosts, subcommand, test
from autotest.client.shared import error
# pylint: disable=E0611
from autotest.client.tests.virt.virttest import utils_misc, cartesian_config
# pylint: disable=E0611
from autotest.client.tests.virt.virttest import bootstrap, utils_params
from autotest.client.tests.virt.virttest import data_dir, asset


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


class multihost_migration_mix(test.test):
    version = 2

    def run_once(self, machines, extra_params, cycles):
        VIRT_TYPE = 'qemu'
        VIRT_DIR = data_dir.get_root_dir()
        TEST_DIR = data_dir.get_backend_dir(VIRT_TYPE)
        PROV_DIR = data_dir.get_test_provider_dir('io-github-autotest-qemu')
        SHARED_DIR = os.path.join(VIRT_DIR, 'shared')
        PROV_DIR = os.path.join(PROV_DIR, VIRT_TYPE)

        asset.download_test_provider("io-github-autotest-qemu")
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

        cpu_number = 2 ** 31
        for host in _hosts.itervalues():
            host.at = autotest_remote.Autotest(host.host)
            cpu_number = min(host.host.get_num_cpu(), cpu_number)

        cfg_file = os.path.join(TEST_DIR, "cfg", "multi-host-tests.cfg")
        logging.info("CONFIG FILE: '%s' is used for generating"
                     " configuration." % cfg_file)

        if not os.path.exists(cfg_file):
            specific_subdirs = asset.get_test_provider_subdirs("qemu")[0]
            orig_cfg_file = os.path.join(specific_subdirs, "cfg",
                                         "multi-host-tests.cfg")
            if os.path.exists(orig_cfg_file):
                shutil.copy(orig_cfg_file, cfg_file)
            else:
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

        machine_hold_vm = machines[0]

        logging.info("")
        for i, params in enumerate(test_dicts):
            logging.info("Test    %d:  %s" % (i, params.get("shortname")))
        logging.info("")

        test_dicts = parser.get_dicts()

        test_dicts_ar = [x for x in map(lambda x: utils_params.Params(x), test_dicts)]

        if not test_dicts_ar:
            error.TestNAError("Impossible start any test with"
                              "this configuration.")

        keep_macs = {}
        random_cpu_number = random.randint(1, cpu_number)
        for params in test_dicts_ar:

            params['hosts'] = ips
            if params.get("use_randome_smp") == "yes":
                params['smp'] = random_cpu_number

            for vm in params.get("vms").split():
                for nic in params.get('nics', "").split():
                    if 'mac_%s_%s' % (nic, vm) not in keep_macs:
                        keep_macs['mac_%s_%s' % (nic, vm)] = generate_mac_address()
                    params['mac_%s_%s' % (nic, vm)] = keep_macs['mac_%s_%s' % (nic, vm)]

            s_host = _hosts[machine_hold_vm]
            s_host.params = params.object_params("host1")
            s_host.params['clone_master'] = "yes"
            s_host.params['hostid'] = ips[machines.index(machine_hold_vm)]

            for host_id, machine in enumerate(machines):
                if machine != machine_hold_vm:
                    host = _hosts[machine]
                    host_name = "host%s" % (host_id + 2)
                    host.params = params.object_params("host%s" % (host_id + 2))
                    params['not_preprocess'] = "yes"
                    host.params['clone_master'] = "no"
                    host.params['hostid'] = ips[host_id]

            # Report the parameters we've received
            logging.debug("Test parameters:")
            keys = params.keys()
            keys.sort()
            for key in keys:
                logging.debug("    %s = %s", key, params[key])

            for machine in machines:
                host = _hosts[machine]
                host.control = CONTROL_MAIN_PART

            if params.get("need_multi_host") == "yes":
                for machine in machines:
                    host = _hosts[machine]
                    host.control += ("job.run_test('virt', tag='%s',"
                                     " params=%s)" %
                                     (host.params['shortname'], host.params))

                logging.debug('Master control file:\n%s',
                              _hosts[machine_hold_vm].control)
                for machine in machines:
                    if machine != machine_hold_vm:
                        host = _hosts[machine]
                        logging.debug('Slave control file:\n%s', host.control)

                commands = []

                for machine in machines:
                    host = _hosts[machine]
                    result_path = os.path.join(self.resultsdir,
                                               host.host.hostname,
                                               host.params["shortname"])
                    cmd = subcommand.subcommand(host.at.run,
                                                [host.control,
                                                 result_path])
                    commands.append(cmd)
            else:
                host = _hosts[machine_hold_vm]
                result_path = os.path.join(self.resultsdir,
                                           host.host.hostname,
                                           host.params["shortname"])
                host.control += ("job.run_test('virt', tag='%s', params=%s)" %
                                 (host.params['shortname'], host.params))
                logging.debug("Run control file:\n %s", host.control)
                commands = [subcommand.subcommand(host.at.run,
                                                  [host.control,
                                                   result_path])]
            try:
                subcommand.parallel(commands)
                if params.get("vm_migrated") == "yes":
                    # This update based on the logical in test case
                    # migration_multi_host. It use the machines[0] as
                    # src and machines[1] as dst. This may need update
                    # based on different design. Just keep the mahinces
                    # and ips list in the right order for following tests.
                    machine_hold_vm = machines[1]
                    ip_hold_vm = ips[1]
                    machines.remove(machine_hold_vm)
                    ips.remove(ip_hold_vm)

                    if params.get("random_dst_host") == "yes":
                        my_random = random.SystemRandom()
                        dst_machine = my_random.choice(machines)
                        dst_ip = ips[machines.index(dst_machine)]
                    else:
                        dst_machine = machines[0]
                        dst_ip = ips[0]
                    machines.remove(dst_machine)
                    ips.remove(dst_ip)

                    machines.insert(0, machine_hold_vm)
                    machines.insert(1, dst_machine)
                    ips.insert(0, ip_hold_vm)
                    ips.insert(1, dst_ip)

            except error.AutoservError, e:
                logging.error(e)
