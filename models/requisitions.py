# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)


class ItemRequisition(models.Model):
    _inherit = "item.requisition"

    requested_by_name = fields.Char(string="Requested By")
    user_name = fields.Char(string='Approved By')
    remote_record_id = fields.Integer("Remote Record ID")
    remote_database_id = fields.Many2one("db.connection", string="Remote Database ID")

    destination1 = fields.Many2one('stock.location', string="Destination", default=lambda self: self._default_destination_location())
    source1 = fields.Many2one('stock.location', string="Source", default=lambda self: self._default_source_location())

    @api.model
    def _default_source_location(self):
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('stacafe_remote_operations.remote_type')

        if remote_type == 'Branch Database':
            # Search for a virtual location marked as the default_virtual_location
            default_virtual_location = self.env['stock.location'].search([('default_virtual_location', '=', True)], limit=1)
            if default_virtual_location:
                return default_virtual_location.id

        # Fallback to existing logic for non-branch databases
        picking_type = self.env['stock.picking.type'].search([('code', '=', 'internal')], limit=1)
        if picking_type and picking_type.default_location_src_id:
            return picking_type.default_location_src_id.id

        return False
    
    @api.model
    def _default_destination_location(self):
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('stacafe_remote_operations.remote_type')

        if remote_type == 'Branch Database':
            # Search for a virtual location marked as the default destination location
            default_destination_location = self.env['stock.location'].search([('default_destination', '=', True)], limit=1)
            if default_destination_location:
                return default_destination_location.id
            else:
                raise models.ValidationError("Please configure a defualt destination location to complete this requisition!")



    def submit_for_approval(self):
        # Call the original method
        super(ItemRequisition, self).submit_for_approval()
        
        # After the original method, push the requisition to the remote server
        self.send_requisition_to_remote()

    def send_requisition_to_remote(self):
        # Get configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('stacafe_remote_operations.remote_type')

        if remote_type != 'Branch Database':
            return True
    
        url = config_parameters.get_param('stacafe_remote_operations.url')
        base_url = config_parameters.get_param('web.base.url')
        db = config_parameters.get_param('stacafe_remote_operations.db')
        username = config_parameters.get_param('stacafe_remote_operations.username')
        password = config_parameters.get_param('stacafe_remote_operations.password')
        partner_id = config_parameters.get_param('stacafe_remote_operations.record_id')

        # Validate settings
        if not all([url, db, username, password, partner_id]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password, Partner Id)")

        # Create XML-RPC connection and send data
        try:
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

            # Prepare the requisition data
            requisition_data = self._prepare_stacafe_remote_operations_values(models, db, uid, password, base_url)            
            _logger.info("Requisition Data: %s", str(requisition_data))
            new_requisition = models.execute_kw(db, uid, password, 'item.requisition', 'create', [requisition_data])
            _logger.info("New Requisition: %s", str(new_requisition))

            self.write({'remote_record_id': int(new_requisition)})

        except Exception as e:
            raise ValidationError("Error while sending requisition data to remote server: {}".format(e))

    def _prepare_stacafe_remote_operations_values(self, models, db, uid, password, current_url):
        self.ensure_one()
        requisition_lines = []

        for line in self.order_line:
            product_id = self._get_remote_id(models, db, uid, password, 'product.product', 'name', line.product_id.name)
            product_uom = self._get_remote_id(models, db, uid, password, 'uom.uom', 'name', line.product_uom.name)

            line_data = {
                'product_id': product_id,
                'product_qty': line.product_qty,
                'product_uom': product_uom,
                'name': line.name,
            }
            requisition_lines.append((0, 0, line_data))

        partner_id = False
        warehouse_id = False
        destination = False
        source = False
        department_id = False

        if self.partner_id:
            partner_id = self._get_remote_id(models, db, uid, password, 'res.partner', 'name', self.partner_id.name)
        
        if self.warehouse_id:
            warehouse_id = self._get_remote_id(models, db, uid, password, 'stock.warehouse', 'name', self.warehouse_id.name)
        
        if self.destination1:
            _logger.info("Self Destination 1 "+str(self.destination1.name))
            destination = self._get_remote_id(models, db, uid, password, 'stock.location', 'name', self.destination1.name)
            _logger.info("Destination After search "+str(destination))

        # if self.source:
        #     source = self._get_remote_id(models, db, uid, password, 'stock.location', 'name', self.source.name)
        
        if self.department_id:
            department_id = self._get_remote_id(models, db, uid, password, 'hr.department', 'name', self.department_id.name)

        # Check if the current database is a branch database
        remote_db_connection_id = False
        if self.env['ir.config_parameter'].sudo().get_param('stacafe_remote_operations.remote_type') == 'Branch Database':
            remote_db_connection_id = self._get_remote_id(models, db, uid, password, 'db.connection', 'url', current_url)

        requisition_vals = {
            'name': self.name,
            # 'requested_by': self.requested_by.id,
            'requested_by_name': self.env.user.name,
            'remote_record_id': self.id,
            'remote_database_id': remote_db_connection_id,
            'department_id': department_id,
            'partner_id': partner_id,
            'warehouse_id': warehouse_id,
            'destination1': destination,
            # 'source': source,
            'delivery_date': self.delivery_date,
            'state': self.state,
            'order_line': requisition_lines,
        }
        return requisition_vals
    
    def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
        remote_record = models.execute_kw(db, uid, password, model, 'search_read', [[(field_name, '=', field_value)]], {'fields': ['id'], 'limit': 1})
        if not remote_record:
            raise ValidationError(_("The record for model '%s' with %s '%s' cannot be found in the remote database.") % (model, field_name, field_value))        
        return remote_record[0]['id']
    
    def approve(self):
        super(ItemRequisition, self).approve()

        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('stacafe_remote_operations.remote_type')
        
        if remote_type != 'Main Database':
            return True

        for rec in self:

            # Ensure remote_record_id and database_connection_id are set
            if not rec.remote_record_id or not rec.remote_database_id:
                continue

            # Get configuration parameters
            db_connection = rec.remote_database_id
            url = db_connection.url
            db = db_connection.db
            username = db_connection.username
            password = db_connection.password

            # Validate settings
            if not all([url, db, username, password]):
                raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

            # Create XML-RPC connection and send data
            try:
                common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
                uid = common.authenticate(db, username, password, {})
                if not uid:
                    raise ValidationError("Failed to authenticate with the remote server.")
                
                models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

                # Send the name of the user that approved the requisition
                user_name = self.env.user.name
                _logger.info("Sending approved user name to remote: %s", user_name)

                models.execute_kw(db, uid, password, 'item.requisition', 'approve', [[rec.remote_record_id]])
                # models.execute_kw(db, uid, password, 'item.requisition', 'write', [[rec.remote_record_id], {'user_name': user_name}])
                _logger.info("Updated user_name for remote record ID: %s", rec.remote_record_id)

            except Exception as e:
                raise ValidationError("Error while updating user name on remote server: {}".format(e))

        return True

    def action_approve_item_requisition(self):
        # Check if the database is configured as "Branch Database" or "Main Database"
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('stacafe_remote_operations.remote_type')

        if self.order_line:
            for data in self:
                if not data.destination1:
                    raise ValidationError(_("Please provide the destination for the items requested!"))

                source_location_id = False
                if remote_type == 'Branch Database':
                    # Get the default virtual location
                    default_virtual_location = self.env['stock.location'].search([('default_virtual_location', '=', True)], limit=1)
                    if not default_virtual_location:
                        raise ValidationError(_("No default virtual location found. Please configure a location with 'default_virtual_location' set to True."))
                    source_location_id = default_virtual_location.id
                else:
                    if data.source1:
                        source_location_id = data.source1.id

                if not source_location_id:
                    raise ValidationError(_("Please provide the source for the items requested!"))

                move_obj = self.env['stock.move']
                pick_obj = self.env["stock.picking"]

                move_vals = []
                for rec in data.order_line:
                    rec.unit_price = rec.product_id.standard_price
                    move_vals.append([0, 0, {
                        'name': '(' + str(rec.product_id.name) + ') requested from ' + str(data.name),
                        'product_id': rec.product_id.id,
                        'product_uom_qty': rec.product_qty,
                        'quantity': rec.product_qty,
                        'price_unit': rec.unit_price if rec.unit_price > 0 else rec.product_id.standard_price,
                        'product_uom': rec.product_uom.id,
                        'location_dest_id': data.destination1.id,
                        'location_id': source_location_id,
                        'requisition_id': data.id,
                    }])

                pick_values = {
                    'note': 'Items requested by ' + str(self.env.user.name),
                    'location_dest_id': data.destination1.id,
                    'location_id': source_location_id,
                    'move_ids_without_package': move_vals,
                    'move_type': 'direct',
                    'picking_type_id': data.get_default_internal_picking_type(),  # check correct picking type ID
                    'origin': data.name,
                    'requisition_id': data.id,
                }

                pick_id = pick_obj.create(pick_values)
                pick_id.action_confirm()
                pick_id.button_validate()
                self.write({'picking_id': pick_id.id})
                data.write({'state': 'done', 'user_complete': self.env.uid})

                if remote_type == 'Main Database' and data.remote_record_id or data.remote_database_id:

                    custom_stock_quant_obj = self.env['custom.stock.quant']
                    for rec in data.order_line:
                        custom_stock_quant_obj.create({
                            'product_id': rec.product_id.id,
                            'product_uom_id': rec.product_uom.id,
                            'date': date.today(),
                            'quantity': rec.product_qty,
                            'unit_price': rec.unit_price if rec.unit_price > 0 else rec.product_id.standard_price,
                            'location_id': source_location_id,
                            'destination_id': data.destination1.id,
                        })

                    self._call_remote_approve()

            return True
        else:
            raise ValidationError(_('You must provide at least one product to complete this request'))

    def _call_remote_approve(self):
        for data in self:
            # Ensure remote_record_id and remote_database_id are set
            if not data.remote_record_id or not data.remote_database_id:
                raise ValidationError(_("Remote record ID or remote database connection is not set."))

            # Get the remote connection details
            db_connection = data.remote_database_id
            url = db_connection.url
            db = db_connection.db
            username = db_connection.username
            password = db_connection.password

            # Validate settings
            if not all([url, db, username, password]):
                raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

            try:
                # Create XML-RPC connection and call the remote method
                common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
                uid = common.authenticate(db, username, password, {})
                if not uid:
                    raise ValidationError("Failed to authenticate with the remote server.")
                
                models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

                # Search for corresponding remote order lines in a single query
                domain = [('order_id', '=', data.remote_record_id)]
                remote_lines = models.execute_kw(db, uid, password, 'item.requisition.order.line', 'search_read', [domain], {'fields': ['id', 'product_id']})

                # Map the remote order lines by product_id
                remote_line_map = {line['product_id'][1]: line['id'] for line in remote_lines}
                _logger.info("Remote line map "+str(remote_line_map))

                # Prepare the updates in batch
                # updates = []
                # for line in data.order_line:
                #     remote_line_id = remote_line_map.get(line.product_id.name)
                #     if remote_line_id:
                #         updates.append((remote_line_id, line.product_id.standard_price))
                #     else:
                #         _logger.warning("No matching remote order line found for Product ID: %s in remote requisition ID: %s", line.product_id.id, data.remote_record_id)
                
                # _logger.info("Updates "+str(updates))

                # Perform the updates individually to avoid passing a list where a single value is expected
                for line in data.order_line:
                    remote_line_id = remote_line_map.get(line.product_id.name)
                    if remote_line_id:
                        models.execute_kw(db, uid, password, 'item.requisition.order.line', 'write', [[remote_line_id], {'unit_price': line.product_id.standard_price}])
                        _logger.info("Updated unit_price for remote order line ID: %s (Product Name: %s)", remote_line_id, line.product_id.name)
                    else:
                        _logger.warning("No matching remote order line found for Product Name: %s in remote requisition ID: %s", line.product_id.name, data.remote_record_id)


                # Call the action_approve_item_requisition method on the remote record
                result = models.execute_kw(db, uid, password, 'item.requisition', 'action_approve_item_requisition', [[data.remote_record_id]])
                _logger.info("Called remote action_approve_item_requisition for record ID: %s", data.remote_record_id)

            except Exception as e:
                raise ValidationError("Error while calling remote action_approve_item_requisition: {}".format(e))

class ItemRequisitionOrderLineInherit(models.Model):
    _inherit = 'item.requisition.order.line'

    unit_price = fields.Float("Unit Price")

class StockLocation(models.Model):
    _inherit = 'stock.location'

    default_virtual_location = fields.Boolean("Default Virtual Location")
    default_destination = fields.Boolean("Default Destination Location")

class CustomStockQuant(models.Model):
    _name = 'custom.stock.quant'
    _rec_name = 'product_id'

    product_id = fields.Many2one('product.product', string="Product")
    product_uom_id = fields.Many2one('uom.uom', string='Unit of Measure')

    date = fields.Date("Date")
    quantity = fields.Float("Quantity")
    unit_price = fields.Float("Unit Price")
    total_value = fields.Float("Total", compute='_compute_total_value',)

    @api.depends('quantity', 'unit_price')
    def _compute_total_value(self):
        for record in self:
            record.total_value = record.quantity * record.unit_price

    location_id = fields.Many2one("stock.location", string="Source Location")
    destination_id = fields.Many2one("stock.location", string="Destination Location")

