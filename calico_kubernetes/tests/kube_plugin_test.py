# Copyright 2015 Metaswitch Networks
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import unittest
from mock import patch, Mock, call
from subprocess import CalledProcessError
import docker
from calico_kubernetes import calico_kubernetes
from pycalico.datastore import IF_PREFIX
from pycalico.datastore_datatypes import Profile


class NetworkPluginTest(unittest.TestCase):

    def setUp(self):
        # Mock out sh so it doesn't fail when trying to find the
        # calicoctl binary (which may not exist)
        with patch('calico_kubernetes.calico_kubernetes.sh.Command',
                   autospec=True) as m_sh:
            self.plugin = calico_kubernetes.NetworkPlugin()

    def test_create(self):
        with patch.object(self.plugin, '_configure_interface',
                    autospec=True) as m_configure_interface, \
                patch.object(self.plugin, '_configure_profile',
                    autospec=True) as m_configure_profile:
            # Set up mock objects
            m_configure_interface.return_value = 'endpt_id'

            # Set up args
            pod_name = 'pod1'
            docker_id = 123456789101112

            # Call method under test
            self.plugin.create(pod_name, docker_id)

            # Assert
            self.assertEqual(pod_name, self.plugin.pod_name)
            self.assertEqual(docker_id, self.plugin.docker_id)
            m_configure_interface.assert_called_once_with()
            m_configure_profile.assert_called_once_with('endpt_id')

    def test_create_error(self):
        with patch.object(self.plugin, '_configure_interface',
                    autospec=True) as m_configure_interface, \
                patch('sys.exit', autospec=True) as m_sys_exit:
            # Set up mock objects
            m_configure_interface.side_effect = CalledProcessError(1,'','')

            # Set up args
            pod_name = 'pod1'
            docker_id = 13

            # Call method under test
            self.plugin.create(pod_name, docker_id)

            # Assert
            m_sys_exit.assert_called_once_with(1)

    def test_delete(self):
        with patch.object(self.plugin, '_datastore_client', autospec=True) as m_datastore_client, \
                patch.object(self.plugin, 'calicoctl', autospec=True) as m_calicoctl:
            # Set up mock objs
            m_datastore_client.profile_exists.return_value = True

            # Set up args
            pod_name = 'pod1'
            docker_id = 123456789123

            # Call method under test
            self.plugin.delete(pod_name, docker_id)

            # Assert
            self.assertEqual(self.plugin.pod_name, pod_name)
            self.assertEqual(self.plugin.docker_id, docker_id)
            m_calicoctl.assert_called_once_with('container', 'remove', docker_id)
            m_datastore_client.remove_profile('pod1_123456789123')

    def test_configure_interface(self):
        with patch.object(self.plugin, '_read_docker_ip',
                    autospec=True) as m_read_docker, \
                patch.object(self.plugin, '_delete_docker_interface',
                    autospec=True) as m_delete_docker_interface, \
                patch.object(self.plugin, 'calicoctl',
                    autospec=True) as m_calicoctl, \
                patch.object(calico_kubernetes, 'generate_cali_interface_name',
                    autospec=True) as m_generate_cali_interface_name, \
                patch.object(self.plugin, '_get_node_ip',
                    autospec=True) as m_get_node_ip, \
                patch.object(calico_kubernetes, 'check_call',
                    autospec=True) as m_check_call,\
                patch.object(self.plugin, '_datastore_client',
                    autospec=True) as m_datastore_client,\
                patch.object(self.plugin, '_docker_client', \
                    autospec=True) as m_docker_client:
            # Set up mock objects
            m_read_docker.return_value = 'docker_ip'
            class ep:
                endpoint_id = 'ep_id'
            m_datastore_client.get_endpoint.return_value = ep
            m_generate_cali_interface_name.return_value = 'interface_name'
            m_get_node_ip.return_value = '1.2.3.4'

            # Call method under test
            return_val = self.plugin._configure_interface()

            # Assert
            m_read_docker.assert_called_once_with()
            m_delete_docker_interface.assert_called_once_with()
            m_calicoctl.assert_called_once_with(
                'container', 'add', self.plugin.docker_id, 'docker_ip', 'eth0')
            m_datastore_client.get_endpoint.assert_called_once_with(
                workload_id=m_docker_client.inspect_container().__getitem__())
            m_generate_cali_interface_name.assert_called_once_with(IF_PREFIX, 'ep_id')
            m_get_node_ip.assert_called_once_with()
            m_check_call.assert_called_once_with(
                ['ip', 'addr', 'add', '1.2.3.4' + '/32',
                'dev', 'interface_name'])
            self.assertEqual(return_val.endpoint_id, 'ep_id')

    def test_get_node_ip(self):
        with patch('calico_kubernetes.calico_kubernetes.get_host_ips',
                   autospec=True) as m_get_host_ips:
            # Set up mock objects
            m_get_host_ips.return_value = ['1.2.3.4','4.2.3.4']

            # Call method under test
            return_val = self.plugin._get_node_ip()

            # Assert
            m_get_host_ips.assert_called_once_with(version=4)
            self.assertEqual(return_val, '1.2.3.4')

    def test_read_docker_ip(self):
        with patch.object(calico_kubernetes, 'check_output',
                          autospec=True) as m_check_output:
            # Set up mock objects
            m_check_output.return_value = '1.2.3.4'

            # Call method under test
            return_val = self.plugin._read_docker_ip()

            # Assert
            m_check_output.assert_called_once_with([
                'docker', 'inspect', '-format', '{{ .NetworkSettings.IPAddress }}',
                self.plugin.docker_id])
            self.assertEqual(return_val, '1.2.3.4')

    def test_delete_docker_interface(self):
        with patch.object(calico_kubernetes, 'check_output',
                          autospec=True) as m_check_output:
            # Set up mock objects
            m_check_output.return_value = 'pid'

            # Call method under test
            self.plugin._delete_docker_interface()

            # Assert call list
            call_1 = call([
                'docker', 'inspect', '-format', '{{ .State.Pid }}',
                self.plugin.docker_id])
            call_2 = call(['mkdir', '-p', '/var/run/netns'])
            call_3 = call(['ln', '-s', '/proc/' + 'pid' + '/ns/net',
                            '/var/run/netns/pid'])
            call_4 = call(['ip', 'netns', 'exec', 'pid', 'ip', 'link', 'del', 'eth0'])
            call_5 = call(['rm', '/var/run/netns/pid'])
            calls = [call_1,call_2,call_3,call_4,call_5]

            m_check_output.assert_has_calls(calls)

    def test_configure_profile(self):
        with patch.object(self.plugin, '_datastore_client',
                    autospec=True) as m_datastore_client, \
                patch.object(self.plugin, '_get_namespace_and_tag',
                    autospec=True) as m_get_namespace_and_tag, \
                patch.object(self.plugin, '_get_pod_config',
                    autospec=True) as m_get_pod_config, \
                patch.object(self.plugin, '_apply_rules',
                    autospec=True) as m_apply_rules, \
                patch.object(self.plugin, '_apply_tags',
                    autospec=True) as m_apply_tags:
            # Set up mock objects
            m_datastore_client.profile_exists.return_value = False
            m_endpoint = Mock()
            m_endpoint.endpoint_id = 'ep_id'
            m_get_pod_config.return_value = 'pod'
            m_get_namespace_and_tag.return_value = 'namespace', 'tag'

            # Set up class members
            self.plugin.pod_name = 'pod_name'
            self.plugin.profile_name = 'name'

            # Call method under test
            self.plugin._configure_profile(m_endpoint)

            # Assert
            m_datastore_client.profile_exists.assert_called_once_with(self.plugin.profile_name)
            m_datastore_client.create_profile.assert_called_once_with(self.plugin.profile_name)
            m_get_pod_config.assert_called_once_with()
            m_apply_rules.assert_called_once_with(self.plugin.profile_name, 'pod')
            m_apply_tags.assert_called_once_with(self.plugin.profile_name, 'pod')
            m_datastore_client.set_profiles_on_endpoint(self.plugin.profile_name, endpoint_id='ep_id')

    def test_get_pod_ports(self):
        # Initialize pod dictionary and expected outcome
        pod = {'spec': {'containers': [{'ports': [1, 2, 3]},{'ports': [4, 5]}]}}
        ports = [1, 2, 3, 4, 5]

        # Call method under test
        return_val = self.plugin._get_pod_ports(pod)

        # Assert
        self.assertEqual(return_val, ports)

    def test_get_pod_ports_no_ports(self):
        """
        Tests for getting ports for a pod, which has no ports.
        Mocks the pod spec reponse from the apiserver such that it
        does not inclue the 'ports' key for each of its containers.
        Asserts not ports are returned and no error is thrown.
        """
        # Initialize pod dictionary and expected outcome
        pod = {'spec': {'containers': [{'':[1, 2, 3]}, {'': [4, 5]}]}}
        ports = []

        # Call method under test
        return_val = self.plugin._get_pod_ports(pod)

        # Assert
        self.assertListEqual(return_val, ports)

    def test_get_pod_config(self):
        with patch.object(self.plugin, '_get_api_path',
                    autospec=True) as m_get_api_path:
            # Set up mock object
            pod1 = {'metadata': {'name': 'pod-1'}}
            pod2 = {'metadata': {'name': 'pod-2'}}
            pod3 = {'metadata': {'name': 'pod-3'}}
            pods = [pod1, pod2, pod3]
            m_get_api_path.return_value = pods

            # Set up class member
            self.plugin.pod_name = 'pod-2'

            # Call method under test
            return_val = self.plugin._get_pod_config()

            # Assert
            self.assertEqual(return_val, pod2)

    def test_get_pod_config_error(self):
        with patch.object(self.plugin, '_get_api_path',
                    autospec=True) as m_get_api_path:
            # Set up mock object and class members
            pod1 = {'metadata': {'name': 'pod-1'}}
            pod2 = {'metadata': {'name': 'pod-2'}}
            pod3 = {'metadata': {'name': 'pod-3'}}
            pods = [pod1, pod2, pod3]
            m_get_api_path.return_value = pods

            # Set up class member
            self.plugin.pod_name = 'pod_4'

            # Call method under test expecting exception
            with self.assertRaises(KeyError):
                self.plugin._get_pod_config()

    def test_get_api_path(self):
        with patch.object(self.plugin, '_get_api_token',
                    autospec=True) as m_api_token, \
                patch('calico_kubernetes.calico_kubernetes.requests.Session',
                    autospec=True) as m_session, \
                patch.object(json, 'loads', autospec=True) as m_json_load:
            # Set up mock objects
            m_api_token.return_value = 'Token'
            m_session_return = Mock()
            m_session_return.headers = Mock()
            m_get_return = Mock()
            m_get_return.text = 'response_body'
            m_session_return.get.return_value = m_get_return
            m_session.return_value = m_session_return

            # Initialize args
            path = 'path/to/api/object'

            # Call method under test
            self.plugin._get_api_path(path)

            # Assert
            m_api_token.assert_called_once_with()
            m_session.assert_called_once_with()
            m_session_return.headers.update.assert_called_once_with(
                {'Authorization': 'Bearer ' + 'Token'})
            m_session_return.get.assert_called_once_with(
                calico_kubernetes.KUBE_API_ROOT + 'path/to/api/object',
                verify=False)
            m_json_load.assert_called_once_with('response_body')

    def test_get_api_token(self):
        with patch('__builtin__.open', autospec=True) as m_open, \
                patch.object(json, 'loads', autospec=True) as m_json:
            # Set up mock objects
            m_open().__enter__().read.return_value = 'json_string'
            m_open.reset_mock()
            m_json.return_value = {'BearerToken' : 'correct_return'}

            # Call method under test
            return_val = self.plugin._get_api_token()

            # Assert
            m_open.assert_called_once_with('/var/lib/kubelet/kubernetes_auth')
            m_json.assert_called_once_with('json_string')
            self.assertEqual(return_val, 'correct_return')

    def test_generate_rules(self):
        pod = {'metadata': {'profile': 'name'}}


        # Call method under test empty annotations/namespace
        return_val = self.plugin._generate_rules(pod)
        # Assert
        self.assertEqual(return_val, ([["allow"]], [["allow"]]))

    def test_apply_rules(self):
        with patch.object(self.plugin, '_generate_rules',
                    autospec=True) as m_generate_rules, \
                patch.object(self.plugin, '_datastore_client',
                    autospec=True) as m_datastore_client, \
                patch.object(self.plugin, 'calicoctl',
                    autospec=True) as m_calicoctl:

            # Set up mock objects
            m_profile = Mock()
            m_datastore_client.get_profile.return_value = m_profile
            m_generate_rules.return_value = ([["allow"]], [["allow"]])
            m_calicoctl.return_value = None
            pod = {'metadata': {'profile': 'name'}}

            # Call method under test
            self.plugin._apply_rules('profile', pod)

            # Assert
            m_datastore_client.get_profile.assert_called_once_with('profile')
            m_calicoctl.assert_has_calls([
                call('profile', 'profile', 'rule', 'remove', 'inbound', '--at=2'),
                call('profile', 'profile', 'rule', 'remove', 'inbound', '--at=1'),
                call('profile', 'profile', 'rule', 'remove', 'outbound', '--at=1')
                ])
            m_generate_rules.assert_called_once_with(pod)
            m_datastore_client.profile_update_rules(m_profile)

    def test_apply_tags(self):
        with patch.object(self.plugin, '_datastore_client', autospec=True) as m_datastore_client:
            # Intialize args
            pod = {'metadata': {'namespace': 'a', 'labels': {1: 2, "2/3": "4_5"}}}
            self.plugin.profile_name = 'profile_name'

            # Set up mock objs
            m_profile = Mock(spec=Profile, name = self.plugin.profile_name)
            m_profile.tags = set()
            m_datastore_client.get_profile.return_value = m_profile

            check_tags = set()
            check_tags.add('namespace_a')
            check_tags.add('a_1_2')
            check_tags.add('a_2_3_4__5')

            # Call method under test
            self.plugin._apply_tags(self.plugin.profile_name, pod)

            # Assert
            m_datastore_client.get_profile.assert_called_once_with(self.plugin.profile_name)
            m_datastore_client.profile_update_tags.assert_called_once_with(m_profile)
            self.assertEqual(m_profile.tags, check_tags)

    def test_apply_tags_error(self):
        with patch.object(self.plugin, '_datastore_client', autospec=True) as m_datastore_client, \
                patch.object(self.plugin, 'calicoctl',autospec=True) as m_calicoctl:
            # Intialize args
            pod = {}
            self.plugin.profile_name = 'profile_name'
            m_datastore_client.get_profile.return_value = Mock()

            # Call method under test
            self.plugin._apply_tags(self.plugin.profile_name, pod)

            # Assert
            self.assertFalse(m_calicoctl.called)
