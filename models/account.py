# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)

class AccountJournal(models.Model):
    _inherit = "account.journal"

    dont_synchronize = fields.Boolean("Don't Synchronize")

class AccountAccount(models.Model):
    _inherit = "account.account"

    substitute_account = fields.Many2one("account.account", string="Substitute Account") 
    
    
class AccountMove(models.Model):
    
    _inherit = 'account.move'
    
    patient = fields.Char("Patient")    