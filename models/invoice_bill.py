# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

import logging
_logger = logging.getLogger(__name__)

class AccountMoveCustom(models.Model):

    _name = 'account.move.custom'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "name"

    name = fields.Char(string="name")
    partner_id = fields.Many2one('res.partner', string="Customer")
    patient = fields.Char(string="Patient")
    invoice_origin = fields.Char(string="Invoice Origin")
    invoice_date = fields.Date(string="Invoice Date")
    invoice_date_due = fields.Date(string="Due Date")
    date = fields.Date(string="Date")
    journal_id = fields.Many2one('account.journal', string="Journal")
    currency_id = fields.Many2one('res.currency', string="Currency")
    company_id = fields.Many2one('res.company', string="Company")
    amount_paid = fields.Monetary('Amount Paid')
    amount_residual = fields.Monetary('Amount Due')
    amount_tax = fields.Monetary('Tax') 
    amount_untaxed = fields.Monetary('Untaxed Amount') 
    amount_total = fields.Monetary('Amount Total') 
    invoice_line_ids = fields.One2many('account.move.custom.line', 'move_id', string="Invoice Line")
    line_ids = fields.One2many('account.move.entry.line', 'move_id', string="Journal Item")
    payment_reference = fields.Char('Payment Reference')
    ref = fields.Char('Reference')
    narration = fields.Html('Terms and Conditions')
    company_currency_id = fields.Many2one('res.currency', string="Company Currency")
    invoice_payment_term_id = fields.Many2one('account.payment.term', string="Payment Terms")
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
    
    source_state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('posted', 'Posted'),
         ('cancel', 'Cancel'),
         ('delete', 'Deleted'),
    ], string="Source State")
    
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
    
    move_count = fields.Integer(string="Move Count", compute="_compute_move_count")
    
    def _compute_move_count(self):
        obj = self.env['account.move']
        for rec in self:
            rec.move_count = obj.search_count([('custom_move_id', '=', rec.id)])
            
    def open_move(self):
        return {
            'name': _('Move Records'),
            'domain': [('custom_move_id', '=', self.id)],
            'view_type': 'form',
            'res_model': 'account.move',
            'view_id': False,
            'view_mode': 'list,form',
            'type': 'ir.actions.act_window',
            } 
        
    def button_rejected(self):
        for rec in self:
            rec.write({
                'custom_state': 'rejected',
            })

    
    def button_draft(self):
        for rec in self:
            rec.write({
                'custom_state': 'draft',
        })

    def button_created(self):
        for rec in self:
            rec.write({
                'custom_state': 'created',
            })
            
            
 
    
   
    def create_bill_move(self):
        for rec in self:
            invoice_line_vals = []
            # Prepare invoice lines
            for inv_line in rec.invoice_line_ids:
                invoice_line_vals.append((0, 0, {
                    'product_id': inv_line.product_id.id,
                    'account_id': inv_line.account_id.id,
                    'name': inv_line.name,
                    'quantity': inv_line.quantity,
                    'product_uom_id': inv_line.product_uom_id.id,
                    'price_unit': inv_line.price_unit,
                    'discount': inv_line.discount,
                    'partner_id': inv_line.partner_id.id,
                    'analytic_distribution': inv_line.analytic_distribution and {
                        str(acc.id): 100 for acc in inv_line.analytic_distribution
                    } or False,
                    'tax_ids': [(6, 0, inv_line.tax_ids.ids)],
                }))

            move_vals = {
                'partner_id': rec.partner_id.id,
                'invoice_date': rec.invoice_date,
                'invoice_date_due': rec.invoice_date_due,
                'date': rec.date,
                'journal_id': rec.journal_id.id,
                'currency_id': rec.currency_id.id,
                'company_id': rec.company_id.id,
                'ref': rec.ref,
                'narration': rec.narration,
                'move_type': rec.move_type,
                'custom_move_id': rec.id,
                'invoice_line_ids': invoice_line_vals,  
            }

            move = self.env['account.move'].sudo().create(move_vals)
            move.action_post()
            rec.write({'account_move_id': move.id,
                        'custom_state': 'created',})

    
    
    def create_account_move(self):
        for rec in self:
            invoice_line_vals = []
            journal_line_vals = []

            # Separate invoice lines
            for inv_line in rec.invoice_line_ids:
                invoice_line_vals.append((0, 0, {
                    'product_id': inv_line.product_id.id,
                    'account_id': inv_line.account_id.id,
                    'name': inv_line.name,
                    'quantity': inv_line.quantity,
                    'product_uom_id': inv_line.product_uom_id.id,
                    'price_unit': inv_line.price_unit,
                    'discount': inv_line.discount,
                    'partner_id': inv_line.partner_id.id,
                    'analytic_distribution': inv_line.analytic_distribution and {str(acc.id): 100 for acc in inv_line.analytic_distribution},
                    'tax_ids': [(6, 0, inv_line.tax_ids.ids)],
                    'display_type': 'product',
                }))

            # Separate journal lines and filter out receivable and income accounts
            for mv_line in rec.line_ids:
                account = self.env['account.account'].browse(mv_line.account_id.id)
                account_type = account.account_type if account else ''
                # Exclude receivable (asset_receivable) and income accounts
                if account_type not in ['asset_receivable', 'income']:
                    journal_line_vals.append((0, 0, {
                        'account_id': mv_line.account_id.id,
                        'name': mv_line.name,
                        'amount_currency': mv_line.amount_currency,
                        'debit': mv_line.debit,
                        'credit': mv_line.credit,
                        'partner_id': mv_line.partner_id.id,
                        'analytic_distribution': mv_line.analytic_distribution and {str(acc.id): 100 for acc in mv_line.analytic_distribution},
                        'tax_ids': [(6, 0, mv_line.tax_ids.ids)],
                        'display_type': 'cogs',
                    }))

            # Step 1: Create move as journal entry
            move_vals = {
                'partner_id': rec.partner_id.id,
                'invoice_date': rec.invoice_date,
                'invoice_date_due': rec.invoice_date_due,
                'date': rec.date,
                'journal_id': rec.journal_id.id,
                'currency_id': rec.currency_id.id,
                'company_id': rec.company_id.id,
                'ref': rec.ref,
                'narration': rec.narration,
                'move_type': rec.move_type,
                'line_ids': [(5, 0, 0)] + journal_line_vals,
                'custom_move_id': rec.id,
            }
            move = self.env['account.move'].sudo().create(move_vals)

            # Step 2: Prevent recomputation
            move.with_context(skip_invoice_line_sync=True).sudo().write({
                'invoice_line_ids': invoice_line_vals,
            })

            # # Step 3 (Optional): Convert to invoice type if needed
            # if rec.move_type != 'entry':
            #     move.sudo().write({'move_type': rec.move_type})

            # Step 4: Save to your record
            rec.write({'account_move_id': move.id,
                        'custom_state': 'created',})
            
            move.action_post()


        

    
    

class AccountMoveCustomLine(models.Model):

    _name = 'account.move.custom.line'
    
    move_id = fields.Many2one('account.move.custom', string="Move")
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
    debit = fields.Monetary('Debit', store=True)
    credit = fields.Monetary('Credit', store=True)
    partner_id = fields.Many2one('res.partner', string="Partner")
    analytic_distribution = fields.Many2many(
    "account.analytic.account", 
        'analytic_distribution_cus_line_rel', 
        'move_custom_line_id', 
        'analytic_account_id', 
        string='Analytic Distribution'
    ) 
    tax_ids = fields.Many2many(
        "account.tax", 
        'account_move_custom_line_tax_rel', 
        'move_custom_line_id', 
        'tax_id', 
        string='Taxes'
    )




    

class AccountMoveEntryLine(models.Model):

    _name = 'account.move.entry.line'
    
    move_id = fields.Many2one('account.move.custom', string="Move")
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
        'analytic_distribution_cus_rel', 
        'move_entry_line_id', 
        'analytic_account_id', 
        string='Analytic Distribution'
    ) 
    tax_ids = fields.Many2many(
        "account.tax", 
        'account_move_entry_tax_rel', 
        'move_entry_line_id', 
        'tax_id', 
        string='Taxes'
    )
    
        
 
