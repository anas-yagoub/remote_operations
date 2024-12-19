# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)


class DBConnections(models.Model):
    _name = 'db.connection'
    _rec_name = 'url'

    url = fields.Char(string='Remote Server URL')
    db = fields.Char(string='Remote Database Name')
    username = fields.Char(string='Username')
    password = fields.Char(string='Password')
    model = fields.Char(string='Remote Model')
    record_id = fields.Char(string='Default Partner Id')

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    remote_type = fields.Selection([
        ('Main Database','Main Database'),
        ('Branch Database','Branch Database')
    ], string="Remote Type", config_parameter='remote_operations.remote_type')

    url = fields.Char(string='Remote Server URL', config_parameter='remote_operations.url')
    db = fields.Char(string='Remote Database Name', config_parameter='remote_operations.db')
    username = fields.Char(string='Username', config_parameter='remote_operations.username')
    password = fields.Char(string='Password', config_parameter='remote_operations.password')
    model = fields.Char(string='Remote Model', config_parameter='remote_operations.model')
    record_id = fields.Char(string='Default Partner Id', config_parameter='remote_operations.record_id')

    def db_connection_action(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Database Connections',
            'view_mode': 'list,form',
            'res_model': 'db.connection',
            'target': 'self',
        }
