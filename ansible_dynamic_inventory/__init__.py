import os
import sys
import json
import re
import requests
import configparser
import subprocess
from collections import MutableMapping
from ansible.parsing.dataloader import DataLoader
from ansible.vars.manager import VariableManager
from ansible.inventory.manager import InventoryManager


def _merge_hash(a, b):
    if a == {} or a == b:
        return b.copy()
    result = a.copy()
    for k, v in b.iteritems():
        if k in result and isinstance(result[k], MutableMapping) and isinstance(v, MutableMapping):
            result[k] = _merge_hash(result[k], v)
        else:
            result[k] = v
    return result


class AnsibleDynamicInventory:

    def __init__(self, config_path):
        config = self._load_config(config_path)
        ansible_static_inventory = self._load_ansible_staitc_inventory(config)
        ansible_static_inventory = self._convert_to_dynamic_inventory(ansible_static_inventory)
        ansible_static_inventory = self._replace_with_consul_service(config, ansible_static_inventory)
        ansible_dynamic_inventory = self._load_ansible_dynamic_inventory(config)
        self.ansible_dynamic_inventory = _merge_hash(ansible_static_inventory, ansible_dynamic_inventory)

    def get_inventory(self):
        return self.ansible_dynamic_inventory

    def _load_config(self, filename):
        if filename is None:
            for v in sys.path:
                path = v + '/ansible_dynamic_inventory/ansible_dynamic_inventory.ini'
                if os.path.exists(path):
                    filename = path
                    break

        config = configparser.ConfigParser()
        config.read(filename)
        return config

    def _load_ansible_staitc_inventory(self, config):
        static_inventory = dict()
        static_inventory_path = config.get("ansible", "static_inventory_path")
        if static_inventory_path:
            static_inventory = InventoryManager(DataLoader(), static_inventory_path)
        return static_inventory

    def _load_ansible_dynamic_inventory(self, config):
        dynamic_inventory = dict()
        dynamic_inventory_path = config.get("ansible", "dynamic_inventory_path")
        if dynamic_inventory_path:
            dynamic_inventory_json = subprocess.check_output([dynamic_inventory_path, '--list'], shell=True)
            dynamic_inventory = json.loads(dynamic_inventory_json)
        return dynamic_inventory

    def _convert_to_dynamic_inventory(aelf, ansible_static_inventory):
        variable_manager = VariableManager(loader=DataLoader(), inventory=ansible_static_inventory)
        ansible_dynamic_inventory = dict()
        for group in ansible_static_inventory.groups.values():
            ansible_dynamic_inventory[group.name] = dict()
            group_hosts = group.get_hosts()
            if len(group_hosts):
                ansible_dynamic_inventory[group.name]["hosts"] = map(str, group_hosts)
            group_vars = group.get_vars()
            if len(group_vars):
                ansible_dynamic_inventory[group.name]["vars"] = group_vars
            group_children = group.child_groups
            if len(group_children):
                ansible_dynamic_inventory[group.name]["children"] = map(str, group_children)
        ansible_dynamic_inventory["_meta"] = dict()
        ansible_dynamic_inventory["_meta"]["hostvars"] = dict()
        for host in ansible_static_inventory.get_hosts():
            ansible_dynamic_inventory["_meta"]["hostvars"][host.name] = variable_manager.get_vars(host=host)
            del(ansible_dynamic_inventory['_meta']['hostvars'][host.name]['groups'])
            del(ansible_dynamic_inventory['_meta']['hostvars'][host.name]['inventory_dir'])
            del(ansible_dynamic_inventory['_meta']['hostvars'][host.name]['inventory_file'])
            del(ansible_dynamic_inventory['_meta']['hostvars'][host.name]['omit'])
        return ansible_dynamic_inventory

    def _replace_with_consul_service(self, config, ansible_dynamic_inventory):
        consul_url = config.get("consul", "url")
        if len(consul_url) == 0:
            return ansible_dynamic_inventory
        for v in ansible_dynamic_inventory.keys():
            res = requests.get(consul_url + "/catalog/service/" + v)
            if res.status_code == requests.codes.ok and len(res.json()):
                ansible_dynamic_inventory[v]["hosts"] = map(lambda x: x["ServiceAddress"], res.json())
        return ansible_dynamic_inventory

    def convert_to_plantuml(self, ansible_dynamic_inventory):
        plantuml_text = groups_text = hosts_text = ""
        group_name_regex = r'[^\w]'
        host_name_regex = r'[^\w\.]'
        for group_name, v in ansible_dynamic_inventory.iteritems():
            group_name = re.sub(group_name_regex, '_', group_name) # use character limit for plantuml
            if group_name == "_meta": # hostvars
                for host_name, _v in v["hostvars"].iteritems():
                    host_name = re.sub(host_name_regex, '_', host_name) # use character limit for plantuml
                    hosts_text += "object " + host_name
                    if _v:
                        hosts_text += " " + json.dumps(_v, indent=2, separators=("", ": "))
                    hosts_text += "\n"
            else: # group definition
                group_text = ""
                group_vars_text = ""
                if "hosts" in v:
                    hostnames = list()
                    for hostname in v['hosts']:
                        hostnames.append(re.sub(host_name_regex, '_', hostname)) # use character limit for plantuml
                    group_join_text = "\n  " + group_name + "_hosts - "
                    group_text += group_join_text + group_join_text.join(hostnames) + "\n"
                if "vars" in v:
                    group_text += "  class " + group_name + "_vars" + "\n"
                    group_vars_text += "class " + group_name + "_vars " + json.dumps(v['vars'], indent=2, separators=("", ": "))  + "\n"
                if "children" in v:
                    for children_group_name in v['children']:
                        children_group_name = re.sub(group_name_regex, '_', children_group_name) # use character limit for plantuml
                        group_text += "  " + group_name + "_children - " + children_group_name + "\n"
                groups_text += "package " + group_name + " {" + group_text + "}\n"
                groups_text += group_vars_text
        plantuml_text = hosts_text + "\n" + groups_text
        plantuml_text = "@startuml\n\n" + plantuml_text + "\n@enduml"
        return plantuml_text
