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
    matching_no = fields.Char(string='#Matching Number Custom')
    custom_move_id = fields.Many2one('account.move.custom', string="Custom Account Move", readonly=True)
    custom_entry_id = fields.Many2one('move.entry.custom', string="Custom Move entry", readonly=True)

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    matching_no = fields.Char(string='#Matching Number Custom')
        
    def _eligible_for_cogs(self):
        self.ensure_one()
        # return self.product_id.is_storable and self.product_id.valuation == 'real_time'
        return False
    
    # @api.model
    # def create(self, vals):
    #     if vals.get('move_id'):
    #         move = self.env['account.move'].browse(vals['move_id'])

    #         if move.custom_move_id and move.move_type in ('out_invoice', 'in_invoice'):
    #             custom_move_lines = move.custom_move_id.line_ids.filtered(
    #                 lambda l: l.account_id.account_type == 'asset_receivable'
    #             )
    #             if custom_move_lines:
    #                 receivable_account = custom_move_lines[0].account_id

    #                 if vals.get('display_type') == 'payment_term':
    #                     vals['account_id'] = receivable_account.id

    #                     if not vals.get('date_maturity'):
    #                         vals['date_maturity'] = move.invoice_date_due or move.invoice_date or fields.Date.today()

    #     return super().create(vals)
    



class AccountBankStatementLines(models.Model):
    _inherit = 'account.bank.statement'

    matching_no = fields.Char(string='#Matching Number Custom')


class AccountBankStatement(models.Model):
    _inherit = 'account.bank.statement'

    matching_no = fields.Char(string='#Matching Number Custom')

    patient = fields.Char("Patient")    
  
    

class AccountPayment(models.Model):
    
    _inherit = "account.payment"

    payment_custom_id = fields.Many2one('account.payment.custom', string="Payment Custom" )
    
class AccountPayment(models.Model):
    
    _inherit = "account.bank.statement.line"

    statement_custom_id = fields.Many2one('bank.statement.line.custom', string="Transfer Payment Custom" )
    
    
    