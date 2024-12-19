from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Iterable
from typing import Iterator
from typing import Literal
from typing import Sequence
from typing import overload

from narwhals._pandas_like.utils import broadcast_align_and_extract_native
from narwhals._pandas_like.utils import calculate_timestamp_date
from narwhals._pandas_like.utils import calculate_timestamp_datetime
from narwhals._pandas_like.utils import int_dtype_mapper
from narwhals._pandas_like.utils import narwhals_to_native_dtype
from narwhals._pandas_like.utils import native_series_from_iterable
from narwhals._pandas_like.utils import native_to_narwhals_dtype
from narwhals._pandas_like.utils import rename
from narwhals._pandas_like.utils import select_columns_by_name
from narwhals._pandas_like.utils import set_axis
from narwhals._pandas_like.utils import to_datetime
from narwhals.dependencies import is_numpy_scalar
from narwhals.typing import CompliantSeries
from narwhals.utils import Implementation
from narwhals.utils import import_dtypes_module

if TYPE_CHECKING:
    from types import ModuleType

    from typing_extensions import Self

    from narwhals._pandas_like.dataframe import PandasLikeDataFrame
    from narwhals.dtypes import DType
    from narwhals.utils import Version

PANDAS_TO_NUMPY_DTYPE_NO_MISSING = {
    "Int64": "int64",
    "int64[pyarrow]": "int64",
    "Int32": "int32",
    "int32[pyarrow]": "int32",
    "Int16": "int16",
    "int16[pyarrow]": "int16",
    "Int8": "int8",
    "int8[pyarrow]": "int8",
    "UInt64": "uint64",
    "uint64[pyarrow]": "uint64",
    "UInt32": "uint32",
    "uint32[pyarrow]": "uint32",
    "UInt16": "uint16",
    "uint16[pyarrow]": "uint16",
    "UInt8": "uint8",
    "uint8[pyarrow]": "uint8",
    "Float64": "float64",
    "float64[pyarrow]": "float64",
    "Float32": "float32",
    "float32[pyarrow]": "float32",
}
PANDAS_TO_NUMPY_DTYPE_MISSING = {
    "Int64": "float64",
    "int64[pyarrow]": "float64",
    "Int32": "float64",
    "int32[pyarrow]": "float64",
    "Int16": "float64",
    "int16[pyarrow]": "float64",
    "Int8": "float64",
    "int8[pyarrow]": "float64",
    "UInt64": "float64",
    "uint64[pyarrow]": "float64",
    "UInt32": "float64",
    "uint32[pyarrow]": "float64",
    "UInt16": "float64",
    "uint16[pyarrow]": "float64",
    "UInt8": "float64",
    "uint8[pyarrow]": "float64",
    "Float64": "float64",
    "float64[pyarrow]": "float64",
    "Float32": "float32",
    "float32[pyarrow]": "float32",
}


class PandasLikeSeries(CompliantSeries):
    def __init__(
        self,
        native_series: Any,
        *,
        implementation: Implementation,
        backend_version: tuple[int, ...],
        version: Version,
    ) -> None:
        self._name = native_series.name
        self._native_series = native_series
        self._implementation = implementation
        self._backend_version = backend_version
        self._version = version

    def __native_namespace__(self: Self) -> ModuleType:
        if self._implementation in {
            Implementation.PANDAS,
            Implementation.MODIN,
            Implementation.CUDF,
        }:
            return self._implementation.to_native_namespace()

        msg = f"Expected pandas/modin/cudf, got: {type(self._implementation)}"  # pragma: no cover
        raise AssertionError(msg)

    def __narwhals_series__(self) -> Self:
        return self

    @overload
    def __getitem__(self, idx: int) -> Any: ...

    @overload
    def __getitem__(self, idx: slice | Sequence[int]) -> Self: ...

    def __getitem__(self, idx: int | slice | Sequence[int]) -> Any | Self:
        if isinstance(idx, int) or is_numpy_scalar(idx):
            return self._native_series.iloc[idx]
        return self._from_native_series(self._native_series.iloc[idx])

    def _change_version(self, version: Version) -> Self:
        return self.__class__(
            self._native_series,
            implementation=self._implementation,
            backend_version=self._backend_version,
            version=version,
        )

    def _from_native_series(self, series: Any) -> Self:
        return self.__class__(
            series,
            implementation=self._implementation,
            backend_version=self._backend_version,
            version=self._version,
        )

    @classmethod
    def _from_iterable(
        cls: type[Self],
        data: Iterable[Any],
        name: str,
        index: Any,
        *,
        implementation: Implementation,
        backend_version: tuple[int, ...],
        version: Version,
    ) -> Self:
        return cls(
            native_series_from_iterable(
                data,
                name=name,
                index=index,
                implementation=implementation,
            ),
            implementation=implementation,
            backend_version=backend_version,
            version=version,
        )

    def __len__(self) -> int:
        return self.shape[0]

    @property
    def name(self) -> str:
        return self._name  # type: ignore[no-any-return]

    @property
    def shape(self) -> tuple[int]:
        return self._native_series.shape  # type: ignore[no-any-return]

    @property
    def dtype(self: Self) -> DType:
        return native_to_narwhals_dtype(
            self._native_series, self._version, self._implementation
        )

    def ewm_mean(
        self,
        *,
        com: float | None = None,
        span: float | None = None,
        half_life: float | None = None,
        alpha: float | None = None,
        adjust: bool = True,
        min_periods: int = 1,
        ignore_nulls: bool = False,
    ) -> PandasLikeSeries:
        ser = self._native_series
        mask_na = ser.isna()
        if self._implementation is Implementation.CUDF:
            if (min_periods == 0 and not ignore_nulls) or (not mask_na.any()):
                result = ser.ewm(
                    com=com, span=span, halflife=half_life, alpha=alpha, adjust=adjust
                ).mean()
            else:
                msg = (
                    "cuDF only supports `ewm_mean` when there are no missing values "
                    "or when both `min_period=0` and `ignore_nulls=False`"
                )
                raise NotImplementedError(msg)
        else:
            result = ser.ewm(
                com, span, half_life, alpha, min_periods, adjust, ignore_na=ignore_nulls
            ).mean()
        result[mask_na] = None
        return self._from_native_series(result)

    def scatter(self, indices: int | Sequence[int], values: Any) -> Self:
        if isinstance(values, self.__class__):
            # .copy() is necessary in some pre-2.2 versions of pandas to avoid
            # `values` also getting modified (!)
            _, values = broadcast_align_and_extract_native(self, values)
            values = set_axis(
                values.copy(),
                self._native_series.index[indices],
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        s = self._native_series.copy(deep=True)
        s.iloc[indices] = values
        s.name = self.name
        return self._from_native_series(s)

    def cast(
        self,
        dtype: Any,
    ) -> Self:
        ser = self._native_series
        dtype = narwhals_to_native_dtype(
            dtype, ser.dtype, self._implementation, self._backend_version, self._version
        )
        return self._from_native_series(ser.astype(dtype))

    def item(self: Self, index: int | None = None) -> Any:
        # cuDF doesn't have Series.item().
        if index is None:
            if len(self) != 1:
                msg = (
                    "can only call '.item()' if the Series is of length 1,"
                    f" or an explicit index is provided (Series is of length {len(self)})"
                )
                raise ValueError(msg)
            return self._native_series.iloc[0]
        return self._native_series.iloc[index]

    def to_frame(self) -> PandasLikeDataFrame:
        from narwhals._pandas_like.dataframe import PandasLikeDataFrame

        return PandasLikeDataFrame(
            self._native_series.to_frame(),
            implementation=self._implementation,
            backend_version=self._backend_version,
            version=self._version,
        )

    def to_list(self) -> Any:
        if self._implementation is Implementation.CUDF:
            return self._native_series.to_arrow().to_pylist()
        return self._native_series.to_list()

    def is_between(
        self, lower_bound: Any, upper_bound: Any, closed: str = "both"
    ) -> PandasLikeSeries:
        ser = self._native_series
        if closed == "left":
            res = ser.ge(lower_bound) & ser.lt(upper_bound)
        elif closed == "right":
            res = ser.gt(lower_bound) & ser.le(upper_bound)
        elif closed == "none":
            res = ser.gt(lower_bound) & ser.lt(upper_bound)
        elif closed == "both":
            res = ser.ge(lower_bound) & ser.le(upper_bound)
        else:  # pragma: no cover
            raise AssertionError
        return self._from_native_series(res)

    def is_in(self, other: Any) -> PandasLikeSeries:
        ser = self._native_series
        res = ser.isin(other)
        return self._from_native_series(res)

    def arg_true(self) -> PandasLikeSeries:
        ser = self._native_series
        result = ser.__class__(range(len(ser)), name=ser.name, index=ser.index).loc[ser]
        return self._from_native_series(result)

    def arg_min(self) -> int:
        ser = self._native_series
        if self._implementation is Implementation.PANDAS and self._backend_version < (1,):
            return ser.values.argmin()  # type: ignore[no-any-return]  # noqa: PD011
        return ser.argmin()  # type: ignore[no-any-return]

    def arg_max(self) -> int:
        ser = self._native_series
        if self._implementation is Implementation.PANDAS and self._backend_version < (1,):
            return ser.values.argmax()  # type: ignore[no-any-return]  # noqa: PD011
        return ser.argmax()  # type: ignore[no-any-return]

    # Binary comparisons

    def filter(self, other: Any) -> PandasLikeSeries:
        if not (isinstance(other, list) and all(isinstance(x, bool) for x in other)):
            ser, other = broadcast_align_and_extract_native(self, other)
        else:
            ser = self._native_series
        return self._from_native_series(
            rename(
                ser.loc[other],
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __eq__(self, other: object) -> PandasLikeSeries:  # type: ignore[override]
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__eq__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __ne__(self, other: object) -> PandasLikeSeries:  # type: ignore[override]
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__ne__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __ge__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__ge__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __gt__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__gt__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __le__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__le__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __lt__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__lt__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __and__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__and__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __rand__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__and__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __or__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__or__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __ror__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__or__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __add__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__add__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __radd__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__radd__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __sub__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__sub__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __rsub__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__rsub__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __mul__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__mul__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __rmul__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__rmul__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __truediv__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__truediv__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __rtruediv__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__rtruediv__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __floordiv__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__floordiv__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __rfloordiv__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__rfloordiv__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __pow__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__pow__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __rpow__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__rpow__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __mod__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__mod__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def __rmod__(self, other: Any) -> PandasLikeSeries:
        ser, other = broadcast_align_and_extract_native(self, other)
        return self._from_native_series(
            rename(
                ser.__rmod__(other),
                ser.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    # Unary

    def __invert__(self: PandasLikeSeries) -> PandasLikeSeries:
        ser = self._native_series
        return self._from_native_series(~ser)

    # Reductions

    def any(self) -> Any:
        ser = self._native_series
        return ser.any()

    def all(self) -> Any:
        ser = self._native_series
        return ser.all()

    def min(self) -> Any:
        ser = self._native_series
        return ser.min()

    def max(self) -> Any:
        ser = self._native_series
        return ser.max()

    def sum(self) -> Any:
        ser = self._native_series
        return ser.sum()

    def count(self) -> Any:
        ser = self._native_series
        return ser.count()

    def mean(self) -> Any:
        ser = self._native_series
        return ser.mean()

    def median(self) -> Any:
        from narwhals.exceptions import InvalidOperationError

        if not self.dtype.is_numeric():
            msg = "`median` operation not supported for non-numeric input type."
            raise InvalidOperationError(msg)
        ser = self._native_series
        return ser.median()

    def std(self: Self, *, ddof: int) -> float:
        ser = self._native_series
        return ser.std(ddof=ddof)  # type: ignore[no-any-return]

    def var(self: Self, *, ddof: int) -> float:
        ser = self._native_series
        return ser.var(ddof=ddof)  # type: ignore[no-any-return]

    def skew(self: Self) -> float | None:
        ser = self._native_series
        ser_not_null = ser.dropna()
        if len(ser_not_null) == 0:
            return None
        elif len(ser_not_null) == 1:
            return float("nan")
        elif len(ser_not_null) == 2:
            return 0.0
        else:
            m = ser_not_null - ser_not_null.mean()
            m2 = (m**2).mean()
            m3 = (m**3).mean()
            return m3 / (m2**1.5) if m2 != 0 else float("nan")

    def len(self) -> Any:
        return len(self._native_series)

    # Transformations

    def is_null(self) -> PandasLikeSeries:
        ser = self._native_series
        return self._from_native_series(ser.isna())

    def fill_null(
        self,
        value: Any | None = None,
        strategy: Literal["forward", "backward"] | None = None,
        limit: int | None = None,
    ) -> Self:
        ser = self._native_series
        if value is not None:
            res_ser = self._from_native_series(ser.fillna(value=value))
        else:
            res_ser = self._from_native_series(
                ser.ffill(limit=limit)
                if strategy == "forward"
                else ser.bfill(limit=limit)
            )

        return res_ser

    def drop_nulls(self) -> PandasLikeSeries:
        ser = self._native_series
        return self._from_native_series(ser.dropna())

    def n_unique(self) -> int:
        ser = self._native_series
        return ser.nunique(dropna=False)  # type: ignore[no-any-return]

    def sample(
        self: Self,
        n: int | None = None,
        *,
        fraction: float | None = None,
        with_replacement: bool = False,
        seed: int | None = None,
    ) -> Self:
        ser = self._native_series
        return self._from_native_series(
            ser.sample(n=n, frac=fraction, replace=with_replacement, random_state=seed)
        )

    def abs(self) -> PandasLikeSeries:
        return self._from_native_series(self._native_series.abs())

    def cum_sum(self: Self, *, reverse: bool) -> Self:
        native_series = self._native_series
        result = (
            native_series.cumsum(skipna=True)
            if not reverse
            else native_series[::-1].cumsum(skipna=True)[::-1]
        )
        return self._from_native_series(result)

    def unique(self, *, maintain_order: bool = False) -> PandasLikeSeries:
        # The param `maintain_order` is only here for compatibility with the Polars API
        # and has no effect on the output.
        return self._from_native_series(
            self._native_series.__class__(
                self._native_series.unique(), name=self._native_series.name
            )
        )

    def diff(self) -> PandasLikeSeries:
        return self._from_native_series(self._native_series.diff())

    def shift(self, n: int) -> PandasLikeSeries:
        return self._from_native_series(self._native_series.shift(n))

    def replace_strict(
        self, old: Sequence[Any], new: Sequence[Any], *, return_dtype: DType | None
    ) -> PandasLikeSeries:
        tmp_name = f"{self.name}_tmp"
        dtype = (
            narwhals_to_native_dtype(
                return_dtype,
                self._native_series.dtype,
                self._implementation,
                self._backend_version,
                self._version,
            )
            if return_dtype
            else None
        )
        other = self.__native_namespace__().DataFrame(
            {
                self.name: old,
                tmp_name: self.__native_namespace__().Series(new, dtype=dtype),
            }
        )
        result = self._from_native_series(
            self._native_series.to_frame()
            .merge(other, on=self.name, how="left")[tmp_name]
            .rename(self.name)
        )
        if result.is_null().sum() != self.is_null().sum():
            msg = (
                "replace_strict did not replace all non-null values.\n\n"
                f"The following did not get replaced: {self.filter(~self.is_null() & result.is_null()).unique().to_list()}"
            )
            raise ValueError(msg)
        return result

    def sort(
        self, *, descending: bool = False, nulls_last: bool = False
    ) -> PandasLikeSeries:
        ser = self._native_series
        na_position = "last" if nulls_last else "first"
        return self._from_native_series(
            rename(
                ser.sort_values(ascending=not descending, na_position=na_position),
                self.name,
                implementation=self._implementation,
                backend_version=self._backend_version,
            )
        )

    def alias(self, name: str) -> Self:
        if name != self.name:
            ser = self._native_series
            return self._from_native_series(
                rename(
                    ser,
                    name,
                    implementation=self._implementation,
                    backend_version=self._backend_version,
                )
            )
        return self

    def __array__(self, dtype: Any = None, copy: bool | None = None) -> Any:
        # pandas used to always return object dtype for nullable dtypes.
        # So, we intercept __array__ and pass to `to_numpy` ourselves to make
        # sure an appropriate numpy dtype is returned.
        return self.to_numpy(dtype=dtype, copy=copy)

    def to_numpy(self, dtype: Any = None, copy: bool | None = None) -> Any:
        # the default is meant to be None, but pandas doesn't allow it?
        # https://numpy.org/doc/stable/reference/generated/numpy.ndarray.__array__.html
        copy = copy or self._implementation is Implementation.CUDF
        dtypes = import_dtypes_module(self._version)
        if self.dtype == dtypes.Datetime and self.dtype.time_zone is not None:  # type: ignore[attr-defined]
            s = self.dt.convert_time_zone("UTC").dt.replace_time_zone(None)._native_series
        else:
            s = self._native_series

        has_missing = s.isna().any()
        if has_missing and str(s.dtype) in PANDAS_TO_NUMPY_DTYPE_MISSING:
            if self._implementation is Implementation.PANDAS and self._backend_version < (
                1,
            ):  # pragma: no cover
                kwargs = {}
            else:
                kwargs = {"na_value": float("nan")}
            return s.to_numpy(
                dtype=dtype or PANDAS_TO_NUMPY_DTYPE_MISSING[str(s.dtype)],
                copy=copy,
                **kwargs,
            )
        if not has_missing and str(s.dtype) in PANDAS_TO_NUMPY_DTYPE_NO_MISSING:
            return s.to_numpy(
                dtype=dtype or PANDAS_TO_NUMPY_DTYPE_NO_MISSING[str(s.dtype)],
                copy=copy,
            )
        return s.to_numpy(dtype=dtype, copy=copy)

    def to_pandas(self) -> Any:
        if self._implementation is Implementation.PANDAS:
            return self._native_series
        elif self._implementation is Implementation.CUDF:
            return self._native_series.to_pandas()
        elif self._implementation is Implementation.MODIN:
            return self._native_series._to_pandas()
        msg = f"Unknown implementation: {self._implementation}"  # pragma: no cover
        raise AssertionError(msg)

    # --- descriptive ---
    def is_duplicated(self: Self) -> Self:
        res = self._native_series.duplicated(keep=False)
        res = rename(
            res,
            self.name,
            implementation=self._implementation,
            backend_version=self._backend_version,
        )
        return self._from_native_series(res)

    def is_empty(self: Self) -> bool:
        return self._native_series.empty  # type: ignore[no-any-return]

    def is_unique(self: Self) -> Self:
        res = rename(
            ~self._native_series.duplicated(keep=False),
            self.name,
            implementation=self._implementation,
            backend_version=self._backend_version,
        )
        return self._from_native_series(res)

    def null_count(self: Self) -> int:
        return self._native_series.isna().sum()  # type: ignore[no-any-return]

    def is_first_distinct(self: Self) -> Self:
        res = rename(
            ~self._native_series.duplicated(keep="first"),
            self.name,
            implementation=self._implementation,
            backend_version=self._backend_version,
        )
        return self._from_native_series(res)

    def is_last_distinct(self: Self) -> Self:
        res = rename(
            ~self._native_series.duplicated(keep="last"),
            self.name,
            implementation=self._implementation,
            backend_version=self._backend_version,
        )
        return self._from_native_series(res)

    def is_sorted(self: Self, *, descending: bool = False) -> bool:
        if not isinstance(descending, bool):
            msg = f"argument 'descending' should be boolean, found {type(descending)}"
            raise TypeError(msg)

        if descending:
            return self._native_series.is_monotonic_decreasing  # type: ignore[no-any-return]
        else:
            return self._native_series.is_monotonic_increasing  # type: ignore[no-any-return]

    def value_counts(
        self: Self,
        *,
        sort: bool = False,
        parallel: bool = False,
        name: str | None = None,
        normalize: bool = False,
    ) -> PandasLikeDataFrame:
        """Parallel is unused, exists for compatibility."""
        from narwhals._pandas_like.dataframe import PandasLikeDataFrame

        index_name_ = "index" if self._name is None else self._name
        value_name_ = name or ("proportion" if normalize else "count")

        val_count = self._native_series.value_counts(
            dropna=False,
            sort=False,
            normalize=normalize,
        ).reset_index()

        val_count.columns = [index_name_, value_name_]

        if sort:
            val_count = val_count.sort_values(value_name_, ascending=False)

        return PandasLikeDataFrame(
            val_count,
            implementation=self._implementation,
            backend_version=self._backend_version,
            version=self._version,
        )

    def quantile(
        self: Self,
        quantile: float,
        interpolation: Literal["nearest", "higher", "lower", "midpoint", "linear"],
    ) -> Any:
        return self._native_series.quantile(q=quantile, interpolation=interpolation)

    def zip_with(self: Self, mask: Any, other: Any) -> PandasLikeSeries:
        ser, mask = broadcast_align_and_extract_native(self, mask)
        _, other = broadcast_align_and_extract_native(self, other)
        res = ser.where(mask, other)
        return self._from_native_series(res)

    def head(self: Self, n: int) -> Self:
        return self._from_native_series(self._native_series.head(n))

    def tail(self: Self, n: int) -> Self:
        return self._from_native_series(self._native_series.tail(n))

    def round(self: Self, decimals: int) -> Self:
        return self._from_native_series(self._native_series.round(decimals=decimals))

    def to_dummies(
        self: Self, *, separator: str = "_", drop_first: bool = False
    ) -> PandasLikeDataFrame:
        from narwhals._pandas_like.dataframe import PandasLikeDataFrame

        plx = self.__native_namespace__()
        series = self._native_series
        name = str(self._name) if self._name else ""

        null_col_pl = f"{name}{separator}null"

        has_nulls = series.isna().any()
        result = plx.get_dummies(
            series,
            prefix=name,
            prefix_sep=separator,
            drop_first=drop_first,
            # Adds a null column at the end, depending on whether or not there are any.
            dummy_na=has_nulls,
            dtype="int8",
        )
        if has_nulls:
            *cols, null_col_pd = list(result.columns)
            output_order = [null_col_pd, *cols]
            result = rename(
                select_columns_by_name(
                    result, output_order, self._backend_version, self._implementation
                ),
                columns={null_col_pd: null_col_pl},
                implementation=self._implementation,
                backend_version=self._backend_version,
            )

        return PandasLikeDataFrame(
            result,
            implementation=self._implementation,
            backend_version=self._backend_version,
            version=self._version,
        )

    def gather_every(self: Self, n: int, offset: int = 0) -> Self:
        return self._from_native_series(self._native_series.iloc[offset::n])

    def clip(
        self: Self, lower_bound: Any | None = None, upper_bound: Any | None = None
    ) -> Self:
        return self._from_native_series(
            self._native_series.clip(lower_bound, upper_bound)
        )

    def to_arrow(self: Self) -> Any:
        if self._implementation is Implementation.CUDF:
            return self._native_series.to_arrow()

        import pyarrow as pa  # ignore-banned-import()

        return pa.Array.from_pandas(self._native_series)

    def mode(self: Self) -> Self:
        native_series = self._native_series
        result = native_series.mode()
        result.name = native_series.name
        return self._from_native_series(result)

    def cum_count(self: Self, *, reverse: bool) -> Self:
        not_na_series = ~self._native_series.isna()
        result = (
            not_na_series.cumsum()
            if not reverse
            else len(self) - not_na_series.cumsum() + not_na_series - 1
        )
        return self._from_native_series(result)

    def cum_min(self: Self, *, reverse: bool) -> Self:
        native_series = self._native_series
        result = (
            native_series.cummin(skipna=True)
            if not reverse
            else native_series[::-1].cummin(skipna=True)[::-1]
        )
        return self._from_native_series(result)

    def cum_max(self: Self, *, reverse: bool) -> Self:
        native_series = self._native_series
        result = (
            native_series.cummax(skipna=True)
            if not reverse
            else native_series[::-1].cummax(skipna=True)[::-1]
        )
        return self._from_native_series(result)

    def cum_prod(self: Self, *, reverse: bool) -> Self:
        native_series = self._native_series
        result = (
            native_series.cumprod(skipna=True)
            if not reverse
            else native_series[::-1].cumprod(skipna=True)[::-1]
        )
        return self._from_native_series(result)

    def rolling_sum(
        self: Self,
        window_size: int,
        *,
        min_periods: int | None,
        center: bool,
    ) -> Self:
        result = self._native_series.rolling(
            window=window_size, min_periods=min_periods, center=center
        ).sum()
        return self._from_native_series(result)

    def rolling_mean(
        self: Self,
        window_size: int,
        *,
        min_periods: int | None,
        center: bool,
    ) -> Self:
        result = self._native_series.rolling(
            window=window_size, min_periods=min_periods, center=center
        ).mean()
        return self._from_native_series(result)

    def rolling_var(
        self: Self,
        window_size: int,
        *,
        min_periods: int | None,
        center: bool,
        ddof: int,
    ) -> Self:
        result = self._native_series.rolling(
            window=window_size, min_periods=min_periods, center=center
        ).var(ddof=ddof)
        return self._from_native_series(result)

    def rolling_std(
        self: Self,
        window_size: int,
        *,
        min_periods: int | None,
        center: bool,
        ddof: int,
    ) -> Self:
        result = self._native_series.rolling(
            window=window_size, min_periods=min_periods, center=center
        ).std(ddof=ddof)
        return self._from_native_series(result)

    def __iter__(self: Self) -> Iterator[Any]:
        yield from self._native_series.__iter__()

    def __contains__(self: Self, other: Any) -> bool:
        return (  # type: ignore[no-any-return]
            self._native_series.isna().any()
            if other is None
            else (self._native_series == other).any()
        )

    def is_finite(self: Self) -> Self:
        s = self._native_series
        return self._from_native_series((s > float("-inf")) & (s < float("inf")))

    @property
    def str(self) -> PandasLikeSeriesStringNamespace:
        return PandasLikeSeriesStringNamespace(self)

    @property
    def dt(self) -> PandasLikeSeriesDateTimeNamespace:
        return PandasLikeSeriesDateTimeNamespace(self)

    @property
    def cat(self) -> PandasLikeSeriesCatNamespace:
        return PandasLikeSeriesCatNamespace(self)

    @property
    def list(self) -> PandasLikeSeriesListNamespace:
        return PandasLikeSeriesListNamespace(self)


class PandasLikeSeriesCatNamespace:
    def __init__(self, series: PandasLikeSeries) -> None:
        self._compliant_series = series

    def get_categories(self) -> PandasLikeSeries:
        s = self._compliant_series._native_series
        return self._compliant_series._from_native_series(
            s.__class__(s.cat.categories, name=s.name)
        )


class PandasLikeSeriesStringNamespace:
    def __init__(self, series: PandasLikeSeries) -> None:
        self._compliant_series = series

    def len_chars(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.len()
        )

    def replace(
        self, pattern: str, value: str, *, literal: bool = False, n: int = 1
    ) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.replace(
                pat=pattern, repl=value, n=n, regex=not literal
            ),
        )

    def replace_all(
        self, pattern: str, value: str, *, literal: bool = False
    ) -> PandasLikeSeries:
        return self.replace(pattern, value, literal=literal, n=-1)

    def strip_chars(self, characters: str | None) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.strip(characters),
        )

    def starts_with(self, prefix: str) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.startswith(prefix),
        )

    def ends_with(self, suffix: str) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.endswith(suffix),
        )

    def contains(self, pattern: str, *, literal: bool = False) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.contains(
                pat=pattern, regex=not literal
            )
        )

    def slice(self, offset: int, length: int | None = None) -> PandasLikeSeries:
        stop = offset + length if length else None
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.slice(start=offset, stop=stop),
        )

    def to_datetime(self: Self, format: str | None) -> PandasLikeSeries:  # noqa: A002
        return self._compliant_series._from_native_series(
            to_datetime(self._compliant_series._implementation)(
                self._compliant_series._native_series, format=format
            )
        )

    def to_uppercase(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.upper(),
        )

    def to_lowercase(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.str.lower(),
        )


class PandasLikeSeriesDateTimeNamespace:
    def __init__(self, series: PandasLikeSeries) -> None:
        self._compliant_series = series

    def date(self) -> PandasLikeSeries:
        result = self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.date,
        )
        if str(result.dtype).lower() == "object":
            msg = (
                "Accessing `date` on the default pandas backend "
                "will return a Series of type `object`."
                "\nThis differs from polars API and will prevent `.dt` chaining. "
                "Please switch to the `pyarrow` backend:"
                '\ndf.convert_dtypes(dtype_backend="pyarrow")'
            )
            raise NotImplementedError(msg)
        return result

    def year(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.year,
        )

    def month(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.month,
        )

    def day(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.day,
        )

    def hour(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.hour,
        )

    def minute(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.minute,
        )

    def second(self) -> PandasLikeSeries:
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.second,
        )

    def millisecond(self) -> PandasLikeSeries:
        return self.microsecond() // 1000

    def microsecond(self) -> PandasLikeSeries:
        if self._compliant_series._backend_version < (3, 0, 0) and "pyarrow" in str(
            self._compliant_series._native_series.dtype
        ):
            # crazy workaround for https://github.com/pandas-dev/pandas/issues/59154
            import pyarrow.compute as pc  # ignore-banned-import()

            native_series = self._compliant_series._native_series
            arr = native_series.array.__arrow_array__()
            result_arr = pc.add(
                pc.multiply(pc.millisecond(arr), 1000), pc.microsecond(arr)
            )
            result = native_series.__class__(
                native_series.array.__class__(result_arr), name=native_series.name
            )
            return self._compliant_series._from_native_series(result)

        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.microsecond
        )

    def nanosecond(self) -> PandasLikeSeries:
        return (  # type: ignore[no-any-return]
            self.microsecond() * 1_000
            + self._compliant_series._native_series.dt.nanosecond
        )

    def ordinal_day(self) -> PandasLikeSeries:
        ser = self._compliant_series._native_series
        year_start = ser.dt.year
        result = (
            ser.to_numpy().astype("datetime64[D]")
            - (year_start.to_numpy() - 1970).astype("datetime64[Y]")
        ).astype("int32") + 1
        dtype = "Int64[pyarrow]" if "pyarrow" in str(ser.dtype) else "int32"
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.__class__(
                result, dtype=dtype, name=year_start.name
            )
        )

    def _get_total_seconds(self) -> Any:
        if hasattr(self._compliant_series._native_series.dt, "total_seconds"):
            return self._compliant_series._native_series.dt.total_seconds()
        else:  # pragma: no cover
            return (
                self._compliant_series._native_series.dt.days * 86400
                + self._compliant_series._native_series.dt.seconds
                + (self._compliant_series._native_series.dt.microseconds / 1e6)
                + (self._compliant_series._native_series.dt.nanoseconds / 1e9)
            )

    def total_minutes(self) -> PandasLikeSeries:
        s = self._get_total_seconds()
        s_sign = (
            2 * (s > 0).astype(int_dtype_mapper(s.dtype)) - 1
        )  # this calculates the sign of each series element
        s_abs = s.abs() // 60
        if ~s.isna().any():
            s_abs = s_abs.astype(int_dtype_mapper(s.dtype))
        return self._compliant_series._from_native_series(s_abs * s_sign)

    def total_seconds(self) -> PandasLikeSeries:
        s = self._get_total_seconds()
        s_sign = (
            2 * (s > 0).astype(int_dtype_mapper(s.dtype)) - 1
        )  # this calculates the sign of each series element
        s_abs = s.abs() // 1
        if ~s.isna().any():
            s_abs = s_abs.astype(int_dtype_mapper(s.dtype))
        return self._compliant_series._from_native_series(s_abs * s_sign)

    def total_milliseconds(self) -> PandasLikeSeries:
        s = self._get_total_seconds() * 1e3
        s_sign = (
            2 * (s > 0).astype(int_dtype_mapper(s.dtype)) - 1
        )  # this calculates the sign of each series element
        s_abs = s.abs() // 1
        if ~s.isna().any():
            s_abs = s_abs.astype(int_dtype_mapper(s.dtype))
        return self._compliant_series._from_native_series(s_abs * s_sign)

    def total_microseconds(self) -> PandasLikeSeries:
        s = self._get_total_seconds() * 1e6
        s_sign = (
            2 * (s > 0).astype(int_dtype_mapper(s.dtype)) - 1
        )  # this calculates the sign of each series element
        s_abs = s.abs() // 1
        if ~s.isna().any():
            s_abs = s_abs.astype(int_dtype_mapper(s.dtype))
        return self._compliant_series._from_native_series(s_abs * s_sign)

    def total_nanoseconds(self) -> PandasLikeSeries:
        s = self._get_total_seconds() * 1e9
        s_sign = (
            2 * (s > 0).astype(int_dtype_mapper(s.dtype)) - 1
        )  # this calculates the sign of each series element
        s_abs = s.abs() // 1
        if ~s.isna().any():
            s_abs = s_abs.astype(int_dtype_mapper(s.dtype))
        return self._compliant_series._from_native_series(s_abs * s_sign)

    def to_string(self, format: str) -> PandasLikeSeries:  # noqa: A002
        # Polars' parser treats `'%.f'` as pandas does `'.%f'`
        # PyArrow interprets `'%S'` as "seconds, plus fractional seconds"
        # and doesn't support `%f`
        if "pyarrow" not in str(self._compliant_series._native_series.dtype):
            format = format.replace("%S%.f", "%S.%f")
        else:
            format = format.replace("%S.%f", "%S").replace("%S%.f", "%S")
        return self._compliant_series._from_native_series(
            self._compliant_series._native_series.dt.strftime(format)
        )

    def replace_time_zone(self, time_zone: str | None) -> PandasLikeSeries:
        if time_zone is not None:
            result = self._compliant_series._native_series.dt.tz_localize(
                None
            ).dt.tz_localize(time_zone)
        else:
            result = self._compliant_series._native_series.dt.tz_localize(None)
        return self._compliant_series._from_native_series(result)

    def convert_time_zone(self, time_zone: str) -> PandasLikeSeries:
        if self._compliant_series.dtype.time_zone is None:  # type: ignore[attr-defined]
            result = self._compliant_series._native_series.dt.tz_localize(
                "UTC"
            ).dt.tz_convert(time_zone)
        else:
            result = self._compliant_series._native_series.dt.tz_convert(time_zone)
        return self._compliant_series._from_native_series(result)

    def timestamp(self, time_unit: Literal["ns", "us", "ms"] = "us") -> PandasLikeSeries:
        s = self._compliant_series._native_series
        dtype = self._compliant_series.dtype
        is_pyarrow_dtype = "pyarrow" in str(self._compliant_series._native_series.dtype)
        mask_na = s.isna()
        dtypes = import_dtypes_module(self._compliant_series._version)
        if dtype == dtypes.Date:
            # Date is only supported in pandas dtypes if pyarrow-backed
            s_cast = s.astype("Int32[pyarrow]")
            result = calculate_timestamp_date(s_cast, time_unit)
        elif dtype == dtypes.Datetime:
            original_time_unit = dtype.time_unit  # type: ignore[attr-defined]
            if (
                self._compliant_series._implementation is Implementation.PANDAS
                and self._compliant_series._backend_version < (2,)
            ):  # pragma: no cover
                s_cast = s.view("Int64[pyarrow]") if is_pyarrow_dtype else s.view("int64")
            else:
                s_cast = (
                    s.astype("Int64[pyarrow]") if is_pyarrow_dtype else s.astype("int64")
                )
            result = calculate_timestamp_datetime(s_cast, original_time_unit, time_unit)
        else:
            msg = "Input should be either of Date or Datetime type"
            raise TypeError(msg)
        result[mask_na] = None
        return self._compliant_series._from_native_series(result)


class PandasLikeSeriesListNamespace:
    def __init__(self, series: PandasLikeSeries) -> None:
        self._compliant_series = series

    def len(self: Self) -> PandasLikeSeries:
        from narwhals.utils import import_dtypes_module

        native_series = self._compliant_series._native_series
        native_result = native_series.list.len()

        if (
            self._compliant_series._implementation is Implementation.PANDAS
            and self._compliant_series._backend_version < (3, 0)
        ):  # pragma: no cover
            native_result = set_axis(
                rename(
                    native_result,
                    native_series.name,
                    implementation=self._compliant_series._implementation,
                    backend_version=self._compliant_series._backend_version,
                ),
                index=native_series.index,
                implementation=self._compliant_series._implementation,
                backend_version=self._compliant_series._backend_version,
            )
        dtype = narwhals_to_native_dtype(
            dtype=import_dtypes_module(self._compliant_series._version).UInt32(),
            starting_dtype=native_result.dtype,
            implementation=self._compliant_series._implementation,
            backend_version=self._compliant_series._backend_version,
            version=self._compliant_series._version,
        )
        return self._compliant_series._from_native_series(native_result.astype(dtype))