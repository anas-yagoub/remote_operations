# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

import logging
_logger = logging.getLogger(__name__)

class AccountJournalCustom(models.Model):

    _name = 'move.entry.custom'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "name"
    _order = "id desc"


    name = fields.Char(string="name")
    partner_id = fields.Many2one('res.partner', string="Customer")
    patient = fields.Char(string="Patient")
    date = fields.Date(string="Accounting Date")
    journal_id = fields.Many2one('account.journal', string="Journal")
    currency_id = fields.Many2one('res.currency', string="Currency")
    company_id = fields.Many2one('res.company', string="Company")
    line_ids = fields.One2many('move.entry.custom.line', 'move_id', string="Journal Item")
    payment_reference = fields.Char('Payment Reference')
    ref = fields.Char('Reference')
    narration = fields.Html('Terms and Conditions')
    company_currency_id = fields.Many2one('res.currency', string="Company Currency")
    move_type = fields.Selection(selection=[
        ('entry', 'Journal Entry'),
        ('out_invoice', 'Customer Invoice'),
         ('out_refund', 'Customer Credit Note'),
          ('in_invoice', 'Vendor Bill'),
          ('in_refund', 'Vendor Credit Note'),
          ('out_receipt', 'Sales Receipt'),
        ('in_receipt', 'Purchase Receipt'),
    ], string="Move Type")
    
    state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('posted', 'Posted'),
         ('cancel', 'Cancel'),
    ], string="Status")
    
    status_in_payment = fields.Selection(selection=[
        ('not_paid', 'Not Paid'),
        ('in_payment', 'In Payment'),
        ('paid', 'Paid'),
        ('partial', 'Partially Paid'),
        ('reversed', 'Reversed'),
        ('blocked', 'Blocked'),
        ('invoicing_legacy', 'Invoicing App Legacy'),
        ('draft', 'Draft'),
        ('cancel', 'cancel'),
    ], string="Status In Payment")
    
    payment_state = fields.Selection(selection=[
        ('not_paid', 'Not Paid'),
        ('in_payment', 'In Payment'),
        ('paid', 'Paid'),
        ('partial', 'Partially Paid'),
        ('reversed', 'Reversed'),
        ('blocked', 'Blocked'),
        ('invoicing_legacy', 'Invoicing App Legacy'),
       
    ], string="Payment Status")
    
    custom_state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('created', 'Created'),
        ('rejected', 'Rejected'),
        ('cancel', 'Cancel'),
    ], default="draft", string="Custom State")
    account_move_id = fields.Many2one('account.move', string="Related Account Move", readonly=True)
    entry_count = fields.Integer(string="journal Entry Count", compute="_compute_entry_count")
    source_state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('posted', 'Posted'),
         ('cancel', 'Cancel'),
         ('delete', 'Deleted'),
         ('edit', 'edited'),
    ], string="Source State", tracking=True)
    
    def _compute_entry_count(self):
        obj = self.env['account.move']
        for rec in self:
            rec.entry_count = obj.search_count([('custom_entry_id', '=', rec.id)])
            
    def open_journal_entry(self):
        return {
            'name': _('Journal Entry Records'),
            'domain': [('custom_entry_id', '=', self.id)],
            'view_type': 'form',
            'res_model': 'account.move',
            'view_id': False,
            'view_mode': 'list,form',
            'type': 'ir.actions.act_window',
            } 
    
    def button_draft(self):
        for rec in self:
            rec.write({
                'custom_state': 'draft',
            })

    def button_rejected(self):
        for rec in self:
            rec.write({
                'custom_state': 'rejected',
            })
    
    def button_created(self):
        for rec in self:
            rec.write({
                'custom_state': 'created',
            })
            
    def create_journal_entry(self):
        for rec in self:
            journal_line_vals = []
            for line in rec.line_ids:
                journal_line_vals.append((0, 0, {
                    'account_id': line.account_id.id,
                    'name': line.name,
                    'amount_currency': line.amount_currency,
                    'debit': line.debit,
                    'credit': line.credit,
                    'partner_id': line.partner_id.id,
                    'analytic_distribution': line.analytic_distribution and {str(acc.id): 100 for acc in line.analytic_distribution},
                    'tax_ids': [(6, 0, line.tax_ids.ids)],
                }))

            if journal_line_vals:
                move_vals = {
                    'patient': rec.patient,
                    'date': rec.date,
                    'journal_id': rec.journal_id.id,
                    'currency_id': rec.currency_id.id,
                    'company_id': rec.company_id.id,
                    'ref': rec.ref,
                    'narration': rec.narration,
                    'move_type': 'entry',
                    'line_ids': journal_line_vals,
                    'custom_entry_id': rec.id, 
                }
                move = self.env['account.move'].sudo().create(move_vals)
                move.action_post()
                rec.write({
                    'account_move_id': move.id,
                    'custom_state': 'created',
                })


           
    

class AccountJournalCustomLine(models.Model):

    _name = 'move.entry.custom.line'
    
    move_id = fields.Many2one('move.entry.custom', string="Move")
    product_id = fields.Many2one('product.product', string="Product")
    account_id = fields.Many2one('account.account', string="Account")
    name = fields.Char(string="name")
    quantity = fields.Float(string="Quantity")
    product_uom_id = fields.Many2one('uom.uom', string="UoM")
    price_unit = fields.Float(string="Price")
    price_subtotal = fields.Monetary('Amount')
    discount = fields.Float('discount')
    currency_id = fields.Many2one('res.currency', string="Currency")
    amount_currency = fields.Monetary('Amount Currency')
    debit = fields.Monetary('Debit')
    credit = fields.Monetary('Credit')
    partner_id = fields.Many2one('res.partner', string="Partner")
    analytic_distribution = fields.Many2many(
        "account.analytic.account", 
        'analytic_distribution_ent_rel', 
        'entry_line_id', 
        'analytic_account_id', 
        string='Analytic Distribution'
    ) 
    tax_ids = fields.Many2many(
        "account.tax", 
        'account_entry_tax_rel', 
        'entry_line_id', 
        'tax_id', 
        string='Taxes'
    )
