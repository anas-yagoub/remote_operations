# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)

class PosSession(models.Model):
    _inherit = 'pos.session'

    posted_to_remote = fields.Boolean("Posted to remote")

    def action_pos_session_closing_control(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        super(PosSession, self).action_pos_session_closing_control(balancing_account, amount_to_balance, bank_payment_method_diffs)
        self.send_account_moves_to_remote()
        self._create_custom_stock_quant_in_remote()

    def _create_custom_stock_quant_in_remote(self):
        # Check if the database is configured as "Branch Database"
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('stacafe_remote_operations.remote_type')

        if remote_type == 'Branch Database':

            url = config_parameters.get_param('stacafe_remote_operations.url')
            db = config_parameters.get_param('stacafe_remote_operations.db')
            username = config_parameters.get_param('stacafe_remote_operations.username')
            password = config_parameters.get_param('stacafe_remote_operations.password')

            if not all([url, db, username, password]):
                raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

            try:
                # Create XML-RPC connection to the remote database
                common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
                uid = common.authenticate(db, username, password, {})
                if not uid:
                    raise ValidationError("Failed to authenticate with the remote server.")
                
                models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

                # Fetch all pickings associated with this POS session
                pickings = self.env['stock.picking'].search([('pos_session_id', '=', self.id)])

                # Create custom.stock.quant records in the remote database
                for picking in pickings:
                    for move in picking.move_ids_without_package:

                         # Fetch the standard_price from the remote database using product name
                        product_data = models.execute_kw(db, uid, password, 'product.product', 'search_read', [
                            [('name', '=', move.product_id.name)]
                        ], {'fields': ['id','standard_price'], 'limit': 1})
                        
                        if not product_data:
                            raise ValidationError(f"Product {move.product_id.name} not found in the remote database.")

                        remote_product_id = product_data[0]['id']
                        remote_standard_price = product_data[0]['standard_price']
                        product_uom = self._get_remote_id(models, db, uid, password, 'uom.uom', 'name', move.product_uom.name)


                        # Fetch remote location IDs
                        remote_location_id = self._get_remote_id(models, db, uid, password, 'stock.location', 'name', move.location_id.name)
                        remote_location_dest_id = self._get_remote_id(models, db, uid, password, 'stock.location', 'name', move.location_dest_id.name)

                        custom_quant_vals = {
                            'product_id': int(remote_product_id),
                            'product_uom_id': product_uom,
                            'date': date.today(),
                            'quantity': -move.product_uom_qty,  # Quantity should be negative since it's a sale
                            'unit_price': remote_standard_price,  # Use the standard price or another logic
                            'location_id': remote_location_id,
                            'destination_id': remote_location_dest_id,
                        }

                        # Create the custom.stock.quant in the remote database
                        models.execute_kw(db, uid, password, 'custom.stock.quant', 'create', [custom_quant_vals])

            except Exception as e:
                raise ValidationError("Error while creating custom stock quant in remote database: {}".format(e))

    def send_account_moves_to_remote(self):
        # Get configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()

        remote_type = config_parameters.get_param('stacafe_remote_operations.remote_type')
        if remote_type != 'Branch Database':
            _logger.info("Database is not configured as 'Branch Database'. Skipping sending account moves to remote.")
            return
        
        url = config_parameters.get_param('stacafe_remote_operations.url')
        db = config_parameters.get_param('stacafe_remote_operations.db')
        username = config_parameters.get_param('stacafe_remote_operations.username')
        password = config_parameters.get_param('stacafe_remote_operations.password')

        # Validate settings
        if not all([url, db, username, password]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

        # Create XML-RPC connection and send data
        try:
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

            # Get related account.move records
            account_moves = self._get_related_account_moves()
            for move in account_moves:
                if move.journal_id.dont_synchronize:
                    continue
                company_id = self._get_remote_id(models, db, uid, password, 'res.company', 'name', move.journal_id.company_id.name)
                move_data = self._prepare_move_data(models, db, uid, password, move, company_id)
                _logger.info("Account Move Data: %s", str(move_data))
                new_move = models.execute_kw(db, uid, password, 'account.move', 'create', [move_data])
                _logger.info("New Account Move: %s", str(new_move))

                # Post the new move
                models.execute_kw(db, uid, password, 'account.move', 'action_post', [[new_move]])
                _logger.info("Posted Account Move: %s", str(new_move))

            self.write({'posted_to_remote': True})

        except Exception as e:
            raise ValidationError("Error while sending account move data to remote server: {}".format(e))

    def _prepare_move_data(self, models, db, uid, password, move, company_id):
        move_lines = []
        for line in move.line_ids:
            account_to_check = line.account_id.code
            if line.account_id.substitute_account:
                account_to_check = line.account_id.substitute_account.code

            account_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_to_check)
            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', line.currency_id)
            
             # Prepare the analytic distribution
            analytic_distribution = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_distribution)

            move_line_data = {
                'account_id': account_id,
                'name': line.name,
                'debit': line.debit,
                'credit': line.credit,
                'partner_id': self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', line.partner_id),
                'currency_id': currency_id,
                'amount_currency': line.amount_currency,
                'analytic_distribution': analytic_distribution,
            }

            move_lines.append((0, 0, move_line_data))

        move_data = {
            'company_id': company_id,
            'ref': move.ref,
            'date': move.date,
            'move_type': move.move_type,
            'currency_id': currency_id,
            'journal_id': self._get_remote_id(models, db, uid, password, 'account.journal', 'name', move.journal_id.name),
            'line_ids': move_lines,
        }

        return move_data
    
    def _prepare_analytic_distribution(self, models, db, uid, password, local_analytic_distribution):
        remote_analytic_distribution = {}
        
        if local_analytic_distribution:
            for local_analytic_account_id, distribution_percentage in local_analytic_distribution.items():
                local_analytic_account = self.env['account.analytic.account'].browse(int(local_analytic_account_id))
                remote_analytic_account_id = self._get_remote_id(models, db, uid, password, 'account.analytic.account', 'name', local_analytic_account.name)
                remote_analytic_distribution[str(remote_analytic_account_id)] = distribution_percentage

        return remote_analytic_distribution
    
    def _get_remote_company_id(self, models, db, uid, password):
        # Fetch the first company from the remote database
        remote_company = models.execute_kw(db, uid, password, 'res.company', 'search_read', [[]], {'fields': ['id'], 'limit': 1})
        if not remote_company:
            raise ValidationError(_("No company found in the remote database."))
        return remote_company[0]['id']

    def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
        remote_record = models.execute_kw(db, uid, password, model, 'search_read', [[(field_name, '=', field_value)]], {'fields': ['id'], 'limit': 1})
        if not remote_record:
            raise ValidationError(_("The record for model '%s' with %s '%s' cannot be found in the remote database.") % (model, field_name, field_value))
        return remote_record[0]['id']

    def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
        if field:
            return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
        return False
