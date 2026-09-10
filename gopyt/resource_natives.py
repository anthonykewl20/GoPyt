"""Checked opaque buffer natives; VM budgets grant bounded allocation authority."""
from gopyt.resource_budget import ResourceLimitError
from gopyt.resource_buffer import Buffer, BufferView
from gopyt.resource_control import ResourceClosedError
from gopyt.resource_mapping import MappingInitializationError
from gopyt.values import Record, UNIT


def install(table):
    methods = {'read':'read', 'write':'write', 'freeze':'freeze', 'view':'view',
               'subview':'view', 'read_view':'read', 'write_view':'write',
               'close':'close', 'close_view':'close'}
    def implementation(operation):
        def call(vm, args, func):
            created = None
            try:
                vm.check_cancelled()
                if operation in ('allocate', 'map_bytes'):
                    constructor = Buffer if operation == 'allocate' else Buffer.map_bytes
                    created = constructor(vm.resource_budget, args[0], context=vm)
                    created._vm_heap = vm.heap
                    created.type_id = vm.type_id_of('data.buffer.Buffer')
                    result = created
                else:
                    handle = args[0]
                    expected = BufferView if operation in ('subview','read_view','write_view','close_view') else Buffer
                    owner = handle.owner if type(handle) is BufferView else handle
                    if type(handle) is not expected or getattr(owner, '_vm_heap', None) is not vm.heap:
                        from gopyt.vm import Trap
                        from gopyt.ops import TRAP_TYPE
                        raise Trap(TRAP_TYPE)
                    result = getattr(handle, methods[operation])(*args[1:])
                    if operation in ('view','subview'):
                        created = result
                        created.type_id = vm.type_id_of('data.buffer.View')
                    elif operation in ('write','write_view','freeze','close','close_view'):
                        if operation == 'close' and owner._control.snapshot()['cleanup_error']:
                            raise ValueError('resource cleanup incomplete')
                        result = UNIT
                vm.check_cancelled()
                if created is not None:
                    vm.heap.adopt(created)
                return result
            except BaseException as error:
                if isinstance(error, MappingInitializationError):
                    created = error.owner
                    error = error.cause
                if created is not None:
                    # Adoption or the final cancellation check may fail before
                    # the heap owns this handle. Retain it before a fallible close.
                    vm.heap.defer_resource(created)
                    created.close()
                if isinstance(error, (ResourceLimitError,ResourceClosedError,ValueError,MemoryError,OverflowError,OSError)):
                    return Record(vm.type_id_of('core.status.ResourceError'), ['resource operation failed'])
                raise error
        return call
    for name in ('allocate', 'map_bytes', *methods):
        table['data.buffer.'+name] = implementation(name)
